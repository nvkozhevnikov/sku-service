"""Existing queue + LIVE11 matrix; transactional SQL double, never PostgreSQL.

Large replay uses saved parser models with distinct synthetic identities/URLs.
It proves bounded workflow/receipts, not live catalog coverage or throughput.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import json
import os
import hashlib
import threading
from functools import lru_cache
import pytest

from test_partner_pilot import CFG
from test_source_canary import offline_env
from test_authorized_collection import exercise
from test_source_jobs import saved_case
from universal_supplier.control_plane import source_batch as batch
from universal_supplier.control_plane.source_discovery import HOSTS
from universal_supplier.control_plane.queue import PostgresJobQueue, normalized_domain
from universal_supplier.control_plane.source_resume import digest


@lru_cache(maxsize=11)
def fixture_base(code):
    if code == 'partner_st':
        url = 'https://partner-st.ru/item-1/'
    elif code == 'optimum':
        from test_optimum_source import URL
        url = URL
    else:
        _, url, _, _ = saved_case(code)
    return url


def fixture_url(code, i):
    url = fixture_base(code)
    return url.replace('/bmso-460c/', f'/bmso-460c-offline-{i}/') if code == 'beka_mak_tr' else url.rstrip('/') + f'-offline-{i}/'


class Database:
    """Only known SQL forms accepted; transactional rollback and serialized claims."""
    def __init__(self, monkeypatch, counts=7):
        self.jobs = []; self.suppliers = {}; self.known = {}; self.sql = []
        self.lock = threading.RLock(); self.fail_insert = None; self.role=('rc_owner',False); self.gates=set()
        policies = {}
        for i, row in enumerate(batch.matrix_rows(), 1):
            code = row['SOURCE']; host = row['APPROVAL_HOST']
            crawl = {'host_policy_approval': {'host': host, 'reference': row['APPROVAL_REFERENCE']}}
            rate = json.loads(row['RATE_POLICY_JSON'])
            self.suppliers[code] = (i, code, code, 'https://' + HOSTS[code], True, crawl, rate)
            n = int(row['KNOWN_URLS_HISTORICAL']) if counts == 'historical' else counts
            self.known[i] = [(j+1,str(30000+j),fixture_url(code,j),n) for j in range(n)]
            policies[host] = row['APPROVAL_DIGEST_SHA256']
        monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES', json.dumps(policies))
        self.queue = PostgresJobQueue(CFG); self.queue._connect = self.connect

    def connect(self):
        db = self
        class Connection:
            read_only = False
            def __enter__(self): return self
            def __exit__(self,*args): pass
            @contextmanager
            def transaction(self):
                with db.lock:
                    before = deepcopy(db.jobs)
                    try: yield
                    except Exception:
                        db.jobs = before
                        raise
            def cursor(self): return Cursor()
        class Cursor:
            rowcount = 0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self, sql, args=None):
                db.sql.append((sql,args)); self.rows=[]; self.rowcount=0
                if 'pg_control_system' in sql:
                    t = batch.build_preview.__globals__['TARGET']
                    self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'])]
                elif 'pg_advisory_xact_lock' in sql: pass
                elif 'SELECT current_user,rolsuper' in sql: self.rows=[db.role]
                elif 'SELECT id,code,adapter_name' in sql:
                    self.rows=list(db.suppliers.values())
                elif 'SELECT id FROM suppliers' in sql:
                    self.rows=[(s[0],) for s in db.suppliers.values()]
                elif 'SELECT id FROM audit_events' in sql:
                    self.rows=[(1,)] if args[1] in db.gates else []
                elif 'INSERT INTO audit_events' in sql:
                    assert args[1]==batch.BEKA_GATE_ACTION and args[2]=='source_batch'
                    db.gates.add(args[3])
                elif 'SELECT id,status,settings_snapshot FROM crawl_jobs' in sql:
                    self.rows=[(j['id'],j['status'],j['settings_snapshot']) for j in db.jobs
                        if j['settings_snapshot']['supplier_code']=='beka_mak' and j['settings_snapshot']['source_batch']['id']==args[0]]
                elif 'SELECT code, adapter_name, base_url' in sql:
                    self.rows=[s[1:] for s in db.suppliers.values() if s[0]==args[0]]
                elif 'SELECT id FROM crawl_jobs' in sql and 'supplier_id=%s' in sql:
                    self.rows=[(j['id'],) for j in db.jobs if j['supplier_id']==args[0] and j['status'] in ('queued','running')][:1]
                elif 'SELECT j.status,j.supplier_id' in sql:
                    j=db.jobs[args[0]-1]
                    self.rows=[(j['status'],j['supplier_id'],j['settings_snapshot'],j['metadata'])]
                elif 'SELECT j.id,j.status,j.settings_snapshot' in sql:
                    self.rows=[(j['id'],j['status'],j['settings_snapshot'],j['error_message'],j['metadata'])
                        for j in db.jobs if j['settings_snapshot']['source_batch']['id']==args[0]]
                elif 'WITH known AS' in sql:
                    self.rows=db.known[args[0]][:args[1]]
                elif "SELECT id FROM crawl_jobs WHERE settings_snapshot" in sql:
                    self.rows=[(j['id'],) for j in db.jobs if j['settings_snapshot']['source_batch']['id']==args[0]][:1]
                elif 'SELECT id FROM crawl_jobs WHERE supplier_id=ANY' in sql:
                    self.rows=[(j['id'],) for j in db.jobs if j['supplier_id'] in args[0] and j['status'] in ('queued','running')]
                elif 'INSERT INTO crawl_jobs' in sql:
                    if len(db.jobs)==db.fail_insert: raise RuntimeError('INJECTED_INSERT_FAILURE')
                    if len(args)==3: sid, raw, user = args
                    else: sid, _, _, _, raw, user = args
                    snap=json.loads(raw); jid=len(db.jobs)+1
                    db.jobs.append({'id':jid,'supplier_id':sid,'settings_snapshot':snap,'status':'queued',
                        'metadata':{},'error_message':None,'user':user})
                    self.rows=[(jid,)]; self.rowcount=1
                elif "SELECT count(*) FROM crawl_jobs WHERE status='running'" in sql:
                    self.rows=[(sum(j['status']=='running' for j in db.jobs),)]
                elif 'BATCH_WINDOW_EXPIRED' in sql:
                    for j in db.jobs:
                        if j['status']=='queued' and datetime.fromisoformat(j['settings_snapshot']['source_batch']['deadline'])<=datetime.now(timezone.utc):
                            j.update(status='cancelled',error_message='BATCH_WINDOW_EXPIRED')
                elif 'SELECT j.id,j.supplier_id' in sql:
                    active={normalized_domain(db.suppliers[j['settings_snapshot']['supplier_code']][3]) for j in db.jobs if j['status']=='running'}
                    for j in db.jobs:
                        snap=j['settings_snapshot']; code=snap['supplier_code']; env=snap['source_batch']
                        gate=env['id'] in db.gates and any(x['status']=='succeeded' and x['settings_snapshot']['supplier_code']=='beka_mak'
                            and x['settings_snapshot']['source_batch']['phase']=='DISCOVERY'
                            and x['settings_snapshot']['source_batch']['id']==env['id'] for x in db.jobs)
                        supplier=db.suppliers[code]
                        if j['status']=='queued' and supplier[4] and normalized_domain(supplier[3]) not in active and (code=='beka_mak' or gate):
                            self.rows=[(j['id'],j['supplier_id'],code,code,supplier[3],'manual','full','queued',None,snap)]
                            break
                elif "UPDATE crawl_jobs SET status='running'" in sql:
                    db.jobs[args[1]-1]['status']='running'
                elif 'OPERATOR_BATCH_STOP' in sql and "status='cancelled'" in sql:
                    for j in db.jobs:
                        if j['status']=='queued' and j['settings_snapshot']['source_batch']['id']==args[0]:
                            j.update(status='cancelled',error_message='OPERATOR_BATCH_STOP')
                elif 'SELECT supplier_id,id FROM crawl_jobs' in sql:
                    self.rows=[(j['supplier_id'],j['id']) for j in db.jobs if j['status']=='running' and j['settings_snapshot']['source_batch']['id']==args[0]]
                elif "UPDATE crawl_jobs SET error_message='OPERATOR_BATCH_STOP_PENDING'" in sql:
                    for j in db.jobs:
                        if j['status']=='running' and j['settings_snapshot']['source_batch']['id']==args[0]:
                            j['error_message']='OPERATOR_BATCH_STOP_PENDING'
                elif 'RETURNING supplier_id,settings_snapshot' in sql:
                    state, run, error, jid=args; job=db.jobs[jid-1]
                    if job['status']=='running':
                        job.update(status=state,error_message='OPERATOR_BATCH_STOP' if job['error_message']=='OPERATOR_BATCH_STOP_PENDING' else error)
                        self.rows=[(job['supplier_id'],job['settings_snapshot'],job['user'],job['error_message'])]; self.rowcount=1
                elif "UPDATE crawl_jobs SET status='failed'" in sql:
                    db.jobs[args[1]-1].update(status='failed',error_message=args[0])
                elif "SELECT count(*),bool_or" in sql:
                    bid,sid,phase=args
                    jobs=[j for j in db.jobs if j['supplier_id']==sid and j['settings_snapshot']['source_batch']['id']==bid and j['settings_snapshot']['source_batch']['phase']==phase]
                    self.rows=[(len(jobs),any(j['status']=='succeeded' for j in jobs),max(j['settings_snapshot']['source_batch']['attempt'] for j in jobs))]
                else:
                    raise AssertionError('UNEXPECTED_SQL: '+sql)
            def fetchone(self): return self.rows[0] if self.rows else None
            def fetchall(self): return self.rows
        return Connection()


def start(db, **changes):
    preview=db.queue.preview_source_batch()
    now=datetime.now(timezone.utc)
    approval={'confirmation_sha256':preview['confirmation_sha256'],'batch_id':str(uuid4()),
        'operator':'offline QA','approved_at':(now-timedelta(seconds=2)).isoformat(),
        'started_at':(now-timedelta(seconds=1)).isoformat(),'head':batch.current_head()}
    approval.update(changes)
    return db.queue.enqueue_source_batch(**approval)


def finish_beka(db):
    first=db.queue.claim_next(); assert first.supplier_code=='beka_mak'
    db.queue.complete(first.id,'succeeded')
    second=db.queue.claim_next(); assert second.supplier_code=='beka_mak'
    assert second.settings_snapshot['source_batch']['phase']=='DISCOVERY'
    db.queue.complete(second.id,'succeeded')
    confirm_gate(db)


def gate_receipt(db):
    jobs=[j for j in db.jobs if j['settings_snapshot']['supplier_code']=='beka_mak']
    env=jobs[0]['settings_snapshot']['source_batch']
    return {'format':'live11-beka-isolation/v1','batch_id':env['id'],'head':env['head'],
        'beka_job_ids':[j['id'] for j in jobs],'target':deepcopy(batch.TARGET),
        'outside_before_sha256':'a'*64,'outside_after_sha256':'a'*64,
        'mapping_preservation':'PASS','evidence_class':'OPERATOR-CONFIRMED',
        'operator':'OFFLINE SYNTHETIC','reference':'OFFLINE TEST ONLY - NO SQL PROOF'}


def confirm_gate(db, **changes):
    proof=gate_receipt(db); proof.update(changes)
    path=Path(os.environ['SOURCE_RUNNER_EVIDENCE_ROOT'])/'beka-gate.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(proof).encode(); path.write_bytes(raw)
    return batch.confirm_beka_gate(db.queue,proof['batch_id'],path,hashlib.sha256(raw).hexdigest())


def test_matrix_historical_large_preview_exact_scopes_no_mutation(monkeypatch):
    db=Database(monkeypatch,'historical'); preview=db.queue.preview_source_batch()
    phases=preview['package']['phases']
    assert len(phases)==22
    assert sum(len(p['snapshot']['source_input']['entries']) for p in phases)==18954
    assert not db.jobs and not any('INSERT' in sql or 'UPDATE' in sql for sql,_ in db.sql)
    assert phases[0]['snapshot']['crawl_settings']['max_products']==1225
    assert phases[-1]['snapshot']['crawl_settings']['max_products']==800


def test_atomic_roots_next_phase_and_no_duplicate_completion(monkeypatch):
    db=Database(monkeypatch); result=start(db)
    assert len(db.jobs)==11 and result['phases']==22
    assert {j['settings_snapshot']['source_batch']['id'] for j in db.jobs}=={result['batch_id']}
    root=db.queue.claim_next(); assert root.supplier_code=='beka_mak'
    assert db.queue.claim_next() is None  # first complete Beka block
    db.queue.complete(root.id,'succeeded')
    assert len(db.jobs)==12
    following=db.jobs[-1]['settings_snapshot']
    assert following==root.settings_snapshot['source_batch']['next_snapshot']
    with pytest.raises(RuntimeError): db.queue.complete(root.id,'succeeded')
    assert len(db.jobs)==12


def test_status_reads_all_frozen_phases_and_approval(monkeypatch):
    db=Database(monkeypatch); result=start(db)
    output=db.queue.source_batch_status(result['batch_id'])
    assert output['approval']['matrix_sha256']==batch.MATRIX_SHA
    assert output['approval']['head']==batch.current_head()
    assert len(output['phases'])==22 and not output['complete']


def test_insert_failure_rolls_back_whole_batch(monkeypatch):
    db=Database(monkeypatch); db.fail_insert=7
    with pytest.raises(RuntimeError,match='INJECTED'): start(db)
    assert db.jobs==[]


@pytest.mark.parametrize('defect',['disabled','policy','host','cap','digest','duplicate'])
def test_fail_closed_no_partial_enqueue(monkeypatch,defect):
    db=Database(monkeypatch)
    if defect in ('disabled','policy','host'):
        supplier=list(db.suppliers['kami'])
        if defect=='disabled': supplier[4]=False
        elif defect=='policy': supplier[6]={**supplier[6],'min_request_interval_seconds':.01}
        else: supplier[3]='https://unapproved.example'
        db.suppliers['kami']=tuple(supplier)
    if defect=='cap':
        sid=db.suppliers['kami'][0]; db.known[sid]=[(1,'1',fixture_url('kami',0),6501)]
    if defect=='duplicate':
        result=start(db)
        before=deepcopy(db.jobs)
        with pytest.raises(PermissionError,match='ALREADY_EXISTS'):
            start(db,batch_id=result['batch_id'])
        assert db.jobs==before
        return
    with pytest.raises((ValueError,PermissionError)):
        start(db,**({'confirmation_sha256':'0'*64} if defect=='digest' else {}))
    assert db.jobs==[]


def test_parallel_claims_never_exceed_two_across_workers(monkeypatch):
    db=Database(monkeypatch); start(db); finish_beka(db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed=list(pool.map(lambda _:db.queue.claim_next(),range(8)))
    actual=[job for job in claimed if job]
    assert len(actual)==2 and len({job.domain for job in actual})==2
    assert any('NOT EXISTS' in sql and "running.status='running'" in sql for sql,_ in db.sql)


@pytest.mark.parametrize('change',['cap','pages','time','mode','next','head','deadline'])
def test_scope_and_expiry_protection(monkeypatch,change):
    db=Database(monkeypatch); start(db)
    snapshot=deepcopy(next(j['settings_snapshot'] for j in db.jobs if j['settings_snapshot']['supplier_code']=='beka_mak'))
    if change=='cap': snapshot['crawl_settings']['max_products']+=1
    if change=='pages': snapshot['crawl_settings']['max_discovery_pages']+=1
    if change=='time': snapshot['crawl_settings']['max_run_duration_seconds']+=1
    if change=='mode': snapshot['source_input']['mode']='DISCOVERY'
    if change=='next': snapshot['source_batch']['next_snapshot']['crawl_settings']['max_products']+=1
    if change=='head': snapshot['source_batch']['head']='0'*40
    if change=='deadline': snapshot['source_batch']['deadline']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises((ValueError,PermissionError)): batch.validate_snapshot(snapshot)


def test_partial_does_not_start_discovery_and_report_requires_decision(monkeypatch):
    db=Database(monkeypatch); start(db); job=db.queue.claim_next()
    db.jobs[job.id-1]['metadata']={'source_runtime':{'processed':2,'remaining':5,'sql_committed':1,
        'resume_uncertain':['url'],'termination_reason':'UNCERTAIN','resume_available':False}}
    db.queue.complete(job.id,'partial')
    assert len(db.jobs)==11 and db.queue.claim_next() is None
    report=batch.report(db.jobs)
    assert len(report['phases'])==22 and not report['complete']
    row=next(r for r in report['phases'] if r['job_id']==job.id)
    assert row['state']=='требуется решение оператора' and row['sql_receipts']==1
    assert row['price_changes'] is None and row['remaining']==5


def test_resume_keeps_scope_and_limits_children(monkeypatch):
    db=Database(monkeypatch); start(db); job=db.queue.claim_next()
    db.queue.complete(job.id,'partial'); previous=job.settings_snapshot
    child=deepcopy(previous); settings=child['crawl_settings']
    settings.update(collection_mode='RESUME',resume_owner_job_id=job.id,resume_input_sha256='a'*64)
    with db.connect().cursor() as cursor:
        batch.resume_envelope(cursor,previous,child)
    assert child['source_batch']['attempt']==2
    assert digest(batch.phase_scope(child))==previous['source_batch']['scope_sha256']
    db.jobs.extend([{**deepcopy(db.jobs[job.id-1]),'id':12,'settings_snapshot':deepcopy(child)},
                    {**deepcopy(db.jobs[job.id-1]),'id':13,'settings_snapshot':deepcopy(child)}])
    db.jobs[-1]['settings_snapshot']['source_batch']['attempt']=3
    third=db.jobs[-1]['settings_snapshot']
    with db.connect().cursor() as cursor, pytest.raises(PermissionError,match='ATTEMPTS_EXHAUSTED'):
        batch.resume_envelope(cursor,third,deepcopy(third))


def test_real_queue_resume_retains_batch_and_forbids_uncertain(monkeypatch):
    db=Database(monkeypatch); start(db); job=db.queue.claim_next()
    db.queue.complete(job.id,'partial')
    parent=db.jobs[job.id-1]
    original=job.settings_snapshot['source_input']
    parent['metadata']={'source_runtime':{'owner_job_id':job.id,'input_sha256':digest(original),'resume_available':True}}
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.read_input',lambda *a:deepcopy(original))
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    child=db.queue.enqueue(job.supplier_id,trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=job.id)
    snap=db.jobs[-1]['settings_snapshot']
    assert child.enqueued and snap['source_batch']['id']==job.settings_snapshot['source_batch']['id']
    assert snap['source_batch']['attempt']==2 and snap['source_input']==original
    db.jobs[-1]['status']='partial'
    parent['metadata']['source_runtime']['resume_uncertain']=['lost-response']
    with pytest.raises(PermissionError,match='REQUIRES_OPERATOR_DECISION'):
        db.queue.enqueue(job.supplier_id,trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=job.id)


def test_batch_stop_prevents_next_phase_even_if_running_finishes_successfully(monkeypatch):
    db=Database(monkeypatch); result=start(db); job=db.queue.claim_next(); stops=[]
    monkeypatch.setattr(db.queue,'stop_source',lambda sid,jid:stops.append((sid,jid)))
    db.queue.stop_source_batch(result['batch_id'])
    assert stops==[(job.supplier_id,job.id)]
    assert sum(j['status']=='cancelled' for j in db.jobs)==10
    db.queue.complete(job.id,'succeeded')
    assert len(db.jobs)==11 and db.queue.claim_next() is None
    assert not batch.report(db.jobs)['complete']


def test_same_host_alias_keeps_second_slot_for_other_host(monkeypatch):
    db=Database(monkeypatch); start(db); finish_beka(db)
    supplier=list(db.suppliers['vekprom']); supplier[3]='https://www.bekamak.com/'
    db.suppliers['vekprom']=tuple(supplier)
    # Existing normalized domain frozen by queue, including www/port aliases.
    next(j for j in db.jobs if j['settings_snapshot']['supplier_code']=='vekprom')['settings_snapshot']['domain']='bekamak.com'
    first=db.queue.claim_next(); second=db.queue.claim_next()
    assert first.supplier_code=='beka_mak_tr' and second.supplier_code!='vekprom'
    assert first.domain!=second.domain


def test_role_prerequisite_blocks_preview_and_enqueue_without_mutation(monkeypatch):
    db=Database(monkeypatch); db.role=('rc_owner',True)
    with pytest.raises(PermissionError,match='ROLE_PREREQUISITE'):
        db.queue.preview_source_batch()
    assert not db.jobs


def test_original_t0_not_reset_by_enqueue(monkeypatch):
    db=Database(monkeypatch)
    now=datetime.now(timezone.utc); t0=now-timedelta(hours=12)
    start(db,approved_at=(t0-timedelta(seconds=1)).isoformat(),started_at=t0.isoformat())
    envelope=db.jobs[0]['settings_snapshot']['source_batch']
    assert datetime.fromisoformat(envelope['deadline'])==t0+timedelta(hours=96)
    assert envelope['package_sha256']


def test_expired_original_t0_cannot_be_reset_or_enqueued(monkeypatch):
    db=Database(monkeypatch); t0=datetime.now(timezone.utc)-timedelta(hours=97)
    with pytest.raises(ValueError,match='APPROVAL_TIME'):
        start(db,approved_at=t0.isoformat(),started_at=t0.isoformat())
    assert not db.jobs


def test_technical_resume_after_control_completion_failure():
    assert batch.resume_is_safe({'status':'succeeded','remaining':0,'resume_available':False})
    assert not batch.resume_is_safe({'status':'partial','remaining':0,'resume_available':False})
    assert not batch.resume_is_safe({'status':'succeeded','remaining':0,'resume_uncertain':['wire']})


def test_control_completion_rollback_retains_scope_for_technical_resume(monkeypatch):
    db=Database(monkeypatch); start(db); job=db.queue.claim_next()
    original=job.settings_snapshot['source_input']
    db.jobs[job.id-1]['metadata']={'source_runtime':{'owner_job_id':job.id,'input_sha256':digest(original),
        'status':'succeeded','remaining':0,'resume_available':False}}
    db.fail_insert=11
    with pytest.raises(RuntimeError,match='INJECTED'): db.queue.complete(job.id,'succeeded')
    assert db.jobs[job.id-1]['status']=='running' and len(db.jobs)==11
    # Existing recover_stale produces this terminal state after lost heartbeat.
    db.jobs[job.id-1]['status']='stale_failed'; db.fail_insert=None
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.read_input',lambda *a:deepcopy(original))
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    child=db.queue.enqueue(job.supplier_id,trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=job.id)
    claimed=db.queue.claim_next(); assert claimed.id==child.job_id
    db.queue.complete(claimed.id,'succeeded')
    following=db.jobs[-1]['settings_snapshot']
    assert following==job.settings_snapshot['source_batch']['next_snapshot']


def test_real_backend_finish_adds_material_history_metrics(monkeypatch):
    from types import SimpleNamespace
    from universal_supplier.control_plane.source_backend import SourceBackend
    queries=[]
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,sql,args): queries.append((sql,args))
        def fetchall(self): return [({'price':'5','quantity':'2'},{'price':'6','quantity':'1'})]
    class Connection:
        def cursor(self): return Cursor()
        @contextmanager
        def transaction(self): yield
    backend=object.__new__(SourceBackend)
    backend.repo=SimpleNamespace(connection=Connection());backend.run_id=77;backend.is_batch=True
    backend.progress=lambda *a:None
    monkeypatch.setattr('universal_supplier.control_plane.adapter_registry._finish_metadata',lambda *a:None)
    result={'status':'succeeded','found':1,'sql_committed_this_job':1,'errors':0,
        'sql_counts':{'new_products':0,'changed_products':1},'collection_mode':'REFRESH',
        'identity_post_processing':{},'termination_reason':None}
    backend.finish(result)
    assert result['batch_price_changes']==result['batch_stock_changes']==1
    assert queries[0][1]==(77,) and 'crawl_run_id=%s' in queries[0][0]


def test_first_block_waits_for_operator_isolation_receipt(monkeypatch):
    db=Database(monkeypatch); result=start(db)
    first=db.queue.claim_next(); db.queue.complete(first.id,'succeeded')
    second=db.queue.claim_next(); db.queue.complete(second.id,'succeeded')
    assert db.queue.claim_next() is None
    assert db.queue.source_batch_status(result['batch_id'])['beka_gate_confirmation_required']
    with pytest.raises(PermissionError,match='ISOLATION_NOT_CONFIRMED'):
        confirm_gate(db,outside_after_sha256='b'*64)
    assert not db.gates
    with pytest.raises(PermissionError,match='JOB_MISMATCH'):
        confirm_gate(db,beka_job_ids=[999,1000])
    confirm_gate(db)
    assert db.queue.claim_next().supplier_code=='beka_mak_tr'


def test_concurrent_identical_capture_waits_for_writer(monkeypatch):
    import time
    original_open=Path.open
    class DelayedWriter:
        def __init__(self,stream): self.stream=stream
        def __enter__(self): return self
        def __exit__(self,*args): self.stream.close()
        def write(self,data):
            time.sleep(.03)
            return self.stream.write(data)
        def flush(self): return self.stream.flush()
        def fileno(self): return self.stream.fileno()
    def slow_open(path,mode='r',*args,**kwargs):
        stream=original_open(path,mode,*args,**kwargs)
        if mode=='xb' and path.parent.name=='captures' and path.suffix=='.html':
            return DelayedWriter(stream)
        return stream
    monkeypatch.setattr(Path,'open',slow_open)
    result,_,_,_=exercise(monkeypatch,'beka_mak',17,concurrency=4)
    assert result['status']=='succeeded' and result['sql_committed']==17,result


def test_report_does_not_sum_cumulative_resume_receipts():
    root={'supplier_code':'kami','source_batch':{'phase':'REFRESH','next_snapshot':None}}
    rows=[{'id':1,'status':'partial','settings_snapshot':root,'metadata':{'source_runtime':{'sql_committed':40,'processed':40,'remaining':60,'batch_price_changes':2,'batch_stock_changes':3}}},
          {'id':2,'status':'succeeded','settings_snapshot':root,'metadata':{'source_runtime':{'sql_committed':100,'processed':100,'remaining':0,'batch_price_changes':4,'batch_stock_changes':5}}}]
    result=batch.report(rows)['phases']
    row=next(r for r in result if r['phase']=='REFRESH')
    assert row['sql_receipts']==100 and row['price_changes']==6 and row['stock_changes']==8


def test_history_metrics_numeric_format_and_quantity_only():
    result=batch.commercial_change_counts([
        ({'price':'100.000','quantity':'2'},{'price':'100','quantity':'1'}),
        ({'price':'10','availability_normalized':'in_stock'},{'price':'11','availability_normalized':'out_of_stock'}),
        (None,{'price':'5'})])
    assert result=={'batch_price_changes':1,'batch_stock_changes':2}


def test_large_22_phase_saved_models_replay_through_real_runtime(monkeypatch):
    db=Database(monkeypatch,100); start(db); processed=0
    from universal_supplier.control_plane.rate_control import DomainRatePolicy
    monkeypatch.setattr('test_authorized_collection.approved_policy',
        lambda code,*args: DomainRatePolicy.from_mapping(db.suppliers[code][6]))
    from dataclasses import replace
    from universal_supplier.control_plane.queue import CrawlJob
    monkeypatch.setattr('test_authorized_collection.CrawlJob',
        lambda *args,**kwargs: replace(CrawlJob(*args,**kwargs),supplier_id=db.suppliers[args[2]][0]))
    while True:
        job=db.queue.claim_next()
        if job is None: break
        snapshot=job.settings_snapshot; phase=snapshot['source_batch']['phase']
        result,backend,calls,_=exercise(monkeypatch,job.supplier_code,100,mode=phase,
            seconds=snapshot['crawl_settings']['max_run_duration_seconds'],snapshot=snapshot,job_id=job.id)
        assert result['status']=='succeeded',result
        assert result['sql_committed']==100 and len(backend.matches)==1
        assert len(calls)==(101 if phase=='REFRESH' else 102 if job.supplier_code!='kami' else 103)
        db.jobs[job.id-1]['metadata']={'source_runtime':result,'identity_post_processing':result['identity_post_processing']}
        db.queue.complete(job.id,'succeeded'); processed+=100
        if job.supplier_code=='beka_mak' and phase=='DISCOVERY':
            assert db.queue.claim_next() is None
            confirm_gate(db)
    assert len(db.jobs)==22 and processed==2200
    assert batch.report(db.jobs)['complete']
    assert sum(j['metadata']['source_runtime']['sql_committed'] for j in db.jobs)==2200
