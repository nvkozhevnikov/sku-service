"""Saved real job17/run18; no network, PostgreSQL or fabricated LIVE receipts."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import socket

import pytest

from universal_supplier.control_plane import source_recovery as recovery, source_batch as batch
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.control_plane.source_runtime import run_source, LEASED_HOST
from universal_supplier.control_plane.queue import CrawlJob
from test_partner_pilot import CFG
from test_source_runtime import Backend
from test_host_fetch import Clock


def denied(*a,**kw):
    raise AssertionError('OFFLINE_NO_NETWORK_OR_POSTGRES')


@pytest.fixture
def real(monkeypatch,tmp_path):
    from offline_fixture_clock import freeze_runtime_utc
    freeze_runtime_utc(monkeypatch)
    value=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/beka_job17_run18.json.gz').read_bytes()))
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket,'getaddrinfo',denied)
    monkeypatch.setattr('psycopg.connect',denied)
    evidence=tmp_path/'evidence';folder=evidence/'beka_mak/job-17';folder.mkdir(parents=True)
    for name in ('checkpoint','input','manifest'):
        (folder/(name+'.json')).write_bytes(base64.b64decode(value[name+'_bytes_base64']))
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(evidence))
    manifest={'format':'live11-recovery/v1','batch_id':value['data']['DURABLE_MATCHING_RESULT.json']['batch_id'],
        'parents':[{'id':r[0],'supplier_id':r[1],'snapshot':r[4]} for r in value['parents']],
        'input_hashes':[digest(r[4]['source_input']) for r in value['parents']],
        'compatibility':{'head':'offline-reviewed-head','base_head':value['parents'][0][4]['source_batch']['head'],
                         'base_engine':value['input']['engine_sha256']},'artifacts':{}}
    for row in value['parents']:
        manifest['input_hashes'].append(digest(row[4]['source_batch']['next_snapshot']['source_input']))
    envelope=value['parents'][0][4]['source_batch']
    manifest.update(started_at=envelope['started_at'],deadline=envelope['deadline'],matrix_sha256=envelope['matrix_sha256'])
    envelopes=[phase['source_batch'] for row in value['parents'] for phase in (row[4],row[4]['source_batch']['next_snapshot'])]
    manifest['envelopes']=envelopes;manifest['scope_hashes']=[e['scope_sha256'] for e in envelopes]
    for name,data in value['data'].items():
        path=tmp_path/name;path.write_text(json.dumps(data),encoding='utf-8')
        manifest['artifacts'][name]={'path':str(path),'sha256':recovery.file_sha(path)}
    monkeypatch.setattr(recovery,'compatibility',lambda *a:manifest['compatibility'])
    path=tmp_path/'approval.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    sha=recovery.file_sha(path)
    monkeypatch.setenv(recovery.ENV+'_FILE',str(path));monkeypatch.setenv(recovery.ENV+'_SHA256',sha)
    monkeypatch.setenv(recovery.ENV+'_CONFIRM',sha)
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({r['APPROVAL_HOST']:r['APPROVAL_DIGEST_SHA256'] for r in batch.matrix_rows()}))
    return value,manifest,sha


def test_saved_actual109_receipt(real):
    value,manifest,_=real
    result=recovery.validate_saved(manifest,value['data'],value['checkpoint'],value['input'])
    assert result['total']==109 and result['by_source']['beka_mak']['CONFLICT']==48


@pytest.mark.parametrize('mutation',['uncertain','frontier','state','wrong_run','scope','mapping','bucket'])
def test_saved_proof_tampering_stops(real,mutation):
    value,manifest,_=real
    value=deepcopy(value);cp=value['checkpoint'];data=value['data']
    if mutation=='uncertain':cp['inflight']['https://beka-mak.su/product/unknown/']={'state':'UNCERTAIN'}
    if mutation=='frontier':cp['frontier'].append(next(iter(cp['terminal'])))
    if mutation=='state':next(iter(cp['terminal'].values()))['state']='FAILED'
    if mutation=='wrong_run':data['DURABLE_MATCHING_RESULT.json']['run_id']=19
    if mutation=='scope':data['DURABLE_MATCHING_RESULT.json']['external_ids'][0]='other'
    if mutation=='mapping':data['BEFORE.json']['mappings'][0][3]=-1
    if mutation=='bucket':data['DURABLE_MATCHING_RESULT.json']['result']['by_source']['beka_mak']['AUTO']=109
    state=deepcopy(cp);state.pop('checksum');cp['checksum']=digest(state)
    with pytest.raises(PermissionError):recovery.validate_saved(manifest,data,cp,value['input'])


def test_operator_confirmation_and_artifact_sha(real,monkeypatch):
    _,manifest,_=real
    recovery.approval()
    monkeypatch.delenv(recovery.ENV+'_CONFIRM')
    with pytest.raises(PermissionError,match='OPERATOR_CONFIRMATION'):recovery.approval()


def test_modified_durable_artifact_denied(real):
    _,manifest,_=real
    Path(manifest['artifacts']['BEFORE.json']['path']).write_text('{}',encoding='utf-8')
    with pytest.raises(PermissionError,match='EVIDENCE_CHANGED'):recovery.approval()


def test_head_and_input_guards_still_fail_closed(real,monkeypatch):
    value,manifest,_=real
    envelope=deepcopy(value['parents'][0][4]['source_batch'])
    recovery.allow_old_head(envelope)
    envelope['deadline']=(datetime.now(timezone.utc)+timedelta(days=20)).isoformat()
    with pytest.raises(PermissionError,match='ENVELOPE_CHANGED'):recovery.allow_old_head(envelope)
    changed=deepcopy(value['input']);changed['entries'].pop()
    with pytest.raises(PermissionError,match='INPUT_NOT_APPROVED'):recovery.allow_old_input(changed)
    monkeypatch.delenv(recovery.ENV+'_FILE')
    monkeypatch.setattr(batch,'current_head',lambda:'new-head')
    with pytest.raises(PermissionError,match='BATCH_HEAD_CHANGED'):batch.validate_snapshot(value['parents'][0][4])


class ProofCursor:
    """Only real receipt-verifier SELECT shapes; writes are impossible."""
    def __init__(self,value):self.value=value;self.rows=[];self.fail=None
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,query,args=None):
        v=self.value;d=v['data']['VERIFIED_DURABLE.json']
        if 'pg_control_system' in query:
            from universal_supplier.control_plane.source_canary import TARGET
            t=TARGET
            self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'])]
            if 'rolbypassrls' in query:self.rows=[(*self.rows[0],'rc_runtime',False,False)]
        elif 'SELECT current_user' in query:self.rows=[('rc_runtime',False,False) if 'rolbypassrls' in query else ('rc_runtime',False)]
        elif 'md5(coalesce(string_agg' in query:
            table=query.split(' FROM ')[1].split()[0]
            self.rows=[tuple(d['after']['hashes'][table])]
        elif 'SELECT status,crawl_run_id,settings_snapshot' in query:self.rows=[('partial',18,v['parents'][0][4])]
        elif 'count(DISTINCT c.id)' in query:self.rows=[(109,109,109)]
        elif 'sp.id,s.code,sp.external_id,sp.catalog_product_id' in query:self.rows=[tuple(r) for r in v['data']['BEFORE.json']['mappings']]
        elif 'FROM product_matches' in query:self.rows=[tuple(r) for r in d['current_matches']]
        elif 'FROM product_identity_decisions' in query:self.rows=[tuple(r) for r in d['identity_decisions']]
        elif 'FROM review_cases' in query:self.rows=[tuple(r) for r in d['review_cases']]
        else:raise AssertionError(query)
        if self.fail and self.fail in query:self.rows=[]
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


def test_full_fresh_sql_contract_and_stale_sql_rejection(real,monkeypatch):
    value,manifest,sha=real
    calls=[]
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:calls.append(a))
    cursor=ProofCursor(value)
    proof=recovery.validate_matching(cursor,value['parents'][0][4],config=CFG)
    assert proof['approval_sha256']==sha and len(proof['external_ids'])==109 and len(calls)==1
    cursor.fail='FROM product_matches'
    with pytest.raises(PermissionError,match='SQL_MATCHES_CHANGED'):
        recovery.validate_matching(cursor,value['parents'][0][4],config=CFG)


def test_real109_technical_resume_zero_http_ingest_matching(real,monkeypatch,tmp_path):
    value,manifest,sha=real
    cursor=ProofCursor(value)
    # The native receipt contract is tested above; this fake readback backend
    # returns only the real saved 109 capture fingerprints, not synthetic cards.
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    proof=recovery.validate_matching(cursor,value['parents'][0][4],config=CFG)
    monkeypatch.setattr(recovery,'runtime_proof',lambda *a:proof)
    backend=Backend()
    fingerprints={r['receipt']['capture_fingerprint'] for r in value['checkpoint']['terminal'].values()}
    backend.probe=lambda entry,receipt:'COMMITTED' if receipt and receipt['capture_fingerprint'] in fingerprints else 'UNKNOWN'
    backend.persist=denied;backend.matching=denied
    snapshot=deepcopy(value['parents'][0][4])
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=17,resume_input_sha256=digest(value['input']))
    snapshot['source_batch'].update(attempt=2,recovery={k:proof[k] for k in ('kind','parent_job_id','run_id','approval_sha256')})
    job=CrawlJob(28,value['parents'][0][1],'beka_mak','beka_mak','https://beka-mak.su','manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:
        result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=denied,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['status']=='succeeded',result
    assert result['processed']==result['sql_committed']==109
    assert result['sql_committed_this_job']==result['parsed_this_job']==result['remaining']==0
    assert result['request_metrics']['requests_total']==0
    assert result['identity_post_processing']['matching_reused'] is True
    assert result['identity_post_processing']['matching_applied'] is False
    assert not backend.writes and not backend.matches
    # Native successful completion uses the original frozen D, with no widening.
    class AdvanceCursor:
        def execute(self,query,args):
            assert 'INSERT INTO crawl_jobs' in query
            self.inserted=json.loads(args[1])
        def fetchone(self):return (29,)
    advance=AdvanceCursor();batch.advance(advance,job.supplier_id,snapshot)
    assert advance.inserted==value['parents'][0][4]['source_batch']['next_snapshot']
    assert value['runtime']['status']=='partial' and value['runtime']['resume_available'] is False
    (tmp_path/'REPLAY_PROOF.json').write_text(json.dumps({
        'classification':'OFFLINE replay of saved real job17/run18 fixtures; no LIVE',
        'committed':result['sql_committed'],'processed':result['processed'],
        'remaining':result['remaining'],'http_requests':result['request_metrics']['requests_total'],
        'parsed':result['parsed_this_job'],'ingested':result['sql_committed_this_job'],
        'matching_reused':result['identity_post_processing']['matching_reused'],
        'rematching_calls':len(backend.matches),'ingest_calls':len(backend.writes),
        'transport_client_constructor':'raises on invocation; not invoked',
        'native_frozen_discovery_advance':True,'old_partial_and_flag_unchanged':True
    },indent=2),encoding='utf-8')


def test_foreign_parent_and_checkpoint_hash_denied(real,monkeypatch):
    value,_,_=real
    with pytest.raises(PermissionError,match='PARENT_NOT_APPROVED'):
        recovery.validate_matching(ProofCursor(value),value['parents'][0][4],19,18,CFG)
    # Simulate a bad hash without writing any checkpoint, including temp copies.
    original_sha=recovery.file_sha
    monkeypatch.setattr(recovery,'file_sha',lambda path:'0'*64 if Path(path).name=='checkpoint.json' else original_sha(path))
    with pytest.raises(PermissionError,match='CHECKPOINT_CHANGED'):
        recovery.validate_matching(ProofCursor(value),value['parents'][0][4],config=CFG)


class RetryDB:
    def __init__(self,real):
        value,manifest,_=real
        self.value=value
        self.config=CFG;self.rows={r[0]:[r[2],r[3],deepcopy(r[4]),r[1]] for r in value['parents']}
        self.disabled=False;self.fail_on=None;self.inserted=0
    def _connect(self):
        db=self
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            @contextmanager
            def transaction(self):
                before=deepcopy(db.rows);count=db.inserted
                try:yield
                except BaseException:db.rows=before;db.inserted=count;raise
            def cursor(self):return Cursor()
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,query,args=None):
                query=' '.join(query.split())
                self.rows=[]
                if 'pg_control_system' in query:
                    from universal_supplier.control_plane.source_canary import TARGET
                    t=TARGET;self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'])]
                    if 'rolbypassrls' in query:self.rows=[(*self.rows[0],'rc_runtime',False,False)]
                elif 'SELECT current_user' in query:self.rows=[('rc_runtime',False,False) if 'rolbypassrls' in query else ('rc_runtime',False)]
                elif 'pg_advisory_xact_lock' in query:pass
                elif 'SELECT code, adapter_name, base_url' in query:
                    row=next(r for r in db.rows.values() if r[3]==args[0]);s=row[2]
                    self.rows=[(s['supplier_code'],s['adapter_name'],'https://'+s['domain'],not db.disabled,s['crawl_settings'],s['rate_settings'])]
                elif 'SELECT j.status,j.supplier_id' in query:
                    r=db.rows[args[0]];self.rows=[(r[0],r[3],r[2],{'source_runtime':db.value['runtime']})]
                elif 'SELECT count(*),bool_or' in query:
                    selected=[r for r in db.rows.values() if r[3]==args[1] and r[2]['source_batch']['phase']==args[2]]
                    self.rows=[(len(selected),any(r[0]=='succeeded' for r in selected),max(r[2]['source_batch']['attempt'] for r in selected))]
                elif 'SELECT status,crawl_run_id,settings_snapshot,supplier_id' in query:self.rows=[tuple(db.rows[args[0]])]
                elif 'SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs' in query:self.rows=[tuple(db.rows[args[0]][:3])]
                elif 'SELECT status FROM crawl_jobs' in query:self.rows=[(db.rows[args[0]][0],)]
                elif 'SELECT enabled,code,adapter_name,base_url' in query:
                    row=next(r for r in db.rows.values() if r[3]==args[0]);s=row[2]
                    self.rows=[(not db.disabled,s['supplier_code'],s['adapter_name'],'https://'+s['domain'])]
                elif 'SELECT id,settings_snapshot FROM crawl_jobs' in query:
                    self.rows=[(jid,r[2]) for jid,r in sorted(db.rows.items(),reverse=True) if r[3]==args[0] and r[2]['source_batch']['id']==args[1]]
                elif 'SELECT id FROM crawl_jobs WHERE supplier_id=' in query:
                    self.rows=[(jid,) for jid,r in db.rows.items() if r[3]==args[0] and r[0] in ('queued','running')]
                elif 'INSERT INTO crawl_jobs' in query:
                    if db.inserted==db.fail_on:raise RuntimeError('OFFLINE_INSERT_FAILURE')
                    jid=max(db.rows)+1;raw=args[1] if len(args)==3 else args[4]
                    db.rows[jid]=['queued',None,json.loads(raw),args[0]];db.inserted+=1
                    self.rows=[(jid,)]
                else:
                    proof=ProofCursor(db.value);proof.execute(query,args);self.rows=proof.fetchall()
            def fetchone(self):return self.rows[0] if self.rows else None
            def fetchall(self):return self.rows
        return Connection()


def test_ten_cancelled_frozen_roots_retry_idempotent_same_batch(real):
    db=RetryDB(real);original=deepcopy(db.rows)
    ids=recovery.retry_cancelled(db)
    assert len(ids)==10 and db.inserted==10
    assert recovery.retry_cancelled(db)==ids and db.inserted==10
    for parent,child in zip(range(18,28),ids):
        assert db.rows[parent]==original[parent]
        assert batch.phase_scope(db.rows[child][2])==batch.phase_scope(original[parent][2])
        assert db.rows[child][2]['source_batch']['deadline']==original[parent][2]['source_batch']['deadline']
        assert db.rows[child][2]['source_batch']['id']==real[1]['batch_id']


def test_retry_atomic_rollback_and_disabled_guard(real):
    db=RetryDB(real);original=deepcopy(db.rows);db.fail_on=3
    with pytest.raises(RuntimeError,match='OFFLINE_INSERT_FAILURE'):recovery.retry_cancelled(db)
    assert db.rows==original and db.inserted==0
    db.fail_on=None;db.disabled=True
    with pytest.raises(PermissionError,match='SOURCE_NOT_ENABLED'):recovery.retry_cancelled(db)
    assert db.rows==original


def test_cancelled_started_or_changed_scope_is_not_retryable(real):
    db=RetryDB(real);db.rows[18][1]=99
    with pytest.raises(PermissionError,match='CANCELLED_ROOT_CHANGED'):recovery.retry_cancelled(db)
    assert db.inserted==0


def test_native_queue_admits_proof_child_without_changing_old_flags(real,monkeypatch):
    from universal_supplier.control_plane.queue import PostgresJobQueue
    db=RetryDB(real);queue=PostgresJobQueue(CFG);queue._connect=db._connect
    original=deepcopy(db.rows[17])
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    with pytest.raises(PermissionError,match='BATCH_RESUME_REQUIRES_OPERATOR_DECISION'):
        queue.enqueue(original[3],trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=17)
    assert db.rows[17]==original and db.inserted==0
    result=queue.enqueue(original[3],trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=17,recovery_sha256=real[2])
    assert result.status=='queued' and db.rows[17]==original
    child=db.rows[result.job_id][2]
    assert batch.phase_scope(child)==batch.phase_scope(original[2])
    assert child['source_batch']['recovery']['parent_job_id']==17
    assert real[0]['runtime']['resume_available'] is False


class SettingsDB:
    config=CFG
    def __init__(self,before):self.settings=deepcopy(before);self.running=False
    def _connect(self):
        db=self
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            @contextmanager
            def transaction(self):
                before=deepcopy(db.settings)
                try:yield
                except BaseException:db.settings=before;raise
            def cursor(self):return Cursor()
        class Cursor:
            rowcount=0
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,query,args=None):
                self.rows=[]
                if 'pg_control_system' in query:
                    from universal_supplier.control_plane.source_canary import TARGET
                    t=TARGET;self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'])]
                elif 'SELECT current_user' in query:self.rows=[('rc_runtime',False,False) if 'rolbypassrls' in query else ('rc_runtime',False)]
                elif 'SELECT id,code,enabled,rate_settings,crawl_settings' in query:self.rows=deepcopy(db.settings)
                elif 'SELECT id FROM crawl_jobs' in query:self.rows=[(777,)] if db.running else []
                elif 'UPDATE suppliers SET' in query:
                    enabled,rate,crawl,sid,code=args
                    row=next(r for r in db.settings if r[0]==sid and r[1]==code)
                    row[2:]=[enabled,rate.obj,crawl.obj];self.rowcount=1
                elif 'INSERT INTO audit_events' in query:pass
                else:raise AssertionError(query)
            def fetchone(self):return self.rows[0] if self.rows else None
            def fetchall(self):return self.rows
        return Connection()


def session_setup(real):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    import live11_recovery_session as session
    manifest=real[1];manifest['activation_matrix']=batch.matrix_rows()
    before=sorted([[r['supplier_id'],r['snapshot']['supplier_code'],False,{},{}] for r in manifest['parents']],key=lambda r:r[1])
    manifest['settings_before']=before
    return session,manifest,SettingsDB(before)


def test_exact_settings_restored_on_exception(real,tmp_path):
    session,manifest,db=session_setup(real)
    before=deepcopy(db.settings)
    with pytest.raises(RuntimeError,match='offline-stop'):
        with session.settings_session(db,manifest,tmp_path):
            assert all(r[2] for r in db.settings)
            raise RuntimeError('offline-stop')
    assert db.settings==before
    assert json.loads((tmp_path/'SETTINGS_RESTORED.json').read_text())['select_equal']


def test_setup_running_or_drift_never_overwritten(real,tmp_path):
    session,manifest,db=session_setup(real)
    db.running=True
    with pytest.raises(PermissionError,match='FOREIGN_OR_RUNNING'):
        with session.settings_session(db,manifest,tmp_path):raise AssertionError('must not enter')
    assert db.settings==manifest['settings_before']


def test_single_operation_stops_at_existing_beka_gate_and_restores(real,monkeypatch,tmp_path):
    session,manifest,db=session_setup(real)
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(recovery,'approval',lambda:(manifest,real[2]))
    monkeypatch.setattr(recovery,'recover',lambda *a:{'technical_child':28})
    closed=[]
    class Worker:
        def __init__(self,*a,**kw):
            assert kw['max_parallel_supplier_jobs']==2
            self.pool=self
        def tick(self):pass
        def shutdown(self,wait):closed.append(wait)
    monkeypatch.setattr(session,'Orchestrator',Worker)
    db.source_batch_status=lambda *a:{'complete':False,'beka_gate_released':False,'phases':[
        {'source':'beka_mak','state':'завершено','job_id':28},
        {'source':'beka_mak','state':'завершено','job_id':39}]}
    import os
    previous=os.environ.get('STERBRUST_RC_MATCHING_APPLY_APPROVED')
    result=session.run(db)
    assert result['reason']=='BEKA_ISOLATION_CONFIRMATION_REQUIRED'
    assert result['settings_restored'] and db.settings==manifest['settings_before'] and closed==[True]
    assert os.environ.get('STERBRUST_RC_MATCHING_APPLY_APPROVED')==previous


@pytest.mark.parametrize('index,bad',[(0,'other_database'),(1,'other_system'),(6,'unknown_role'),(7,True),(8,True)])
def test_fresh_sql_identity_and_role_rejected(real,monkeypatch,index,bad):
    value=real[0];cursor=ProofCursor(value);execute=cursor.execute
    def changed(query,args=None):
        execute(query,args)
        if 'pg_control_system' in query:
            row=list(cursor.rows[0]);row[index]=bad;cursor.rows=[tuple(row)]
    cursor.execute=changed
    with pytest.raises(PermissionError,match='TARGET_OR_ROLE_CHANGED'):
        recovery.verify_sql(cursor,value['data'],value['checkpoint'],value['input'],CFG)


def test_exact_original_windows_engine_reconstruction(real):
    value=real[0];root=Path(__file__).resolve().parents[1]
    try:
        hashes=recovery.base_files(root,value['parents'][0][4]['source_batch']['head'],value['input']['engine_sha256'])
    except PermissionError as error:
        if str(error)!='RECOVERY_UNCHANGED_FILE_CHANGED':raise
        # A point-in-time proof: it holds only while every non-engine file still
        # equals the batch base commit (b1160fd, 2026-10-22); later work changed them.
        pytest.skip('engine files changed since the LIVE11 base commit; historical reconstruction not reproducible')
    assert digest(hashes)==value['input']['engine_sha256']
    assert 'universal_supplier/automatic_matching.py' in hashes


def test_parser_change_is_not_compatible(monkeypatch):
    monkeypatch.setattr(batch,'current_head',lambda:'new-head')
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a,**kw:'universal_supplier/automatic_matching.py\n')
    with pytest.raises(PermissionError,match='UNREVIEWED_ENGINE_CHANGE'):
        recovery.compatibility(Path('.'),'old-head','old-engine')


def test_worker_rechecks_proof_with_read_only_sql_no_row_locks(real,monkeypatch):
    value,_,sha=real
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    queries=[]
    class Cursor(ProofCursor):
        def execute(self,query,args=None):
            assert 'FOR SHARE' not in query and 'FOR UPDATE' not in query
            queries.append(query);super().execute(query,args)
    class Connection:
        read_only=False
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return Cursor(value)
    monkeypatch.setattr('universal_supplier.control_plane.queue.PostgresJobQueue._connect',lambda *a:Connection())
    snapshot=deepcopy(value['parents'][0][4])
    snapshot['source_batch']['recovery']={'kind':'matching-only','approval_sha256':sha,'parent_job_id':17,'run_id':18}
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=17,resume_input_sha256=digest(value['input']))
    job=CrawlJob(28,11,'beka_mak','beka_mak','https://beka-mak.su','manual','full','running',None,snapshot)
    proof=recovery.runtime_proof(job,CFG)
    assert proof['result']['total']==109 and any('FROM product_matches' in q for q in queries)


@pytest.mark.parametrize('key,bad',[('owner_job_id',6),('remaining',1),('resume_uncertain',['lost']),('input_sha256','0'*64)])
def test_runtime_owner_uncertain_or_manifest_mismatch_cannot_admit(real,key,bad):
    value=real[0];rt=deepcopy(value['runtime']);rt[key]=bad
    with pytest.raises(PermissionError,match='RUNTIME_OWNER_OR_RECEIPTS_CHANGED'):
        recovery.admission_runtime(rt,{'input_sha256':digest(value['input'])})
