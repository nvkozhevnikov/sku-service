"""Real conflicting capture, native runtime/checkpoint; all network/SQL denied."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import gzip
import base64
import pytest

from test_source_runtime import isolation, setup, Backend
from test_source_jobs import saved_case
from test_host_fetch import Clock
from test_source_recovery import real, session_setup
from universal_supplier.http_capture import HttpResponse
from universal_supplier.control_plane import source_recovery as recovery, source_batch as batch
from universal_supplier.control_plane.source_resume import classify, pending_manifest, digest
from universal_supplier.control_plane.source_runtime import run_source, LEASED_HOST

BAD='https://www.bekamak.com/urun/bmdo-120-xs/en'
FIX=Path(__file__).parent/'fixtures/live11_recovery/bmdo120_conflict.html.gz'


def execution(*,bad_status=200,backend=None,bad_count=1,bad_body=None,monkeypatch=None):
    _,job,backend,_,good=setup('beka_mak_tr',backend=backend)
    body,_,_,_=saved_case('beka_mak_tr');bad=gzip.decompress(FIX.read_bytes());clock=Clock();calls=[]
    assert hashlib.sha256(bad).hexdigest()=='3afa05063cf40cec299729514849d78e9309327e3b73357861a0422f097005b8'
    if bad_body is not None:bad=bad_body
    urls=[BAD] if bad_count==1 else [f'https://www.bekamak.com/urun/bad-{n}/en' for n in range(bad_count)]
    snapshot=deepcopy(job.settings_snapshot)
    snapshot['source_input']['entries']=[{'url':u,'source_external_id':'bad-'+str(i)} for i,u in enumerate(urls)]+snapshot['source_input']['entries']
    snapshot['crawl_settings']['max_products']=len(urls)+1
    job=replace(job,settings_snapshot=snapshot)
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            if url.endswith('robots.txt'):return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /')
            return HttpResponse(url,200 if url==good else bad_status,'text/html',body if url==good else bad)
    def run(j=job):
        token=LEASED_HOST.set(j.domain)
        try:return run_source(j,None,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
        finally:LEASED_HOST.reset(token)
    return run,job,backend,calls,good


@pytest.mark.parametrize('status,category',[(200,'identity'),(404,'http')])
def test_bad_url_quarantined_next_url_committed_and_matching(status,category):
    run,job,backend,calls,good=execution(bad_status=status)
    result=run()
    assert result['status']=='succeeded' and not result['collection_complete'],result
    assert result['sql_committed']==1 and result['processed']==2 and result['remaining']==0
    assert len(result['quarantined'])==1 and result['errors']==1 and result['termination_reason'] is None
    receipt=result['quarantined'][0]
    assert receipt['url']==BAD and receipt['state']=='QUARANTINED'
    assert receipt['isolation']['before_ingest'] and receipt['isolation']['category']==category
    assert len(backend.writes)==1 and backend.writes[0].requested_url==good and len(backend.matches)==1
    cp=json.loads(Path(result['checkpoint']).read_text())
    assert set(cp['terminal'])=={BAD,good} and cp['terminal'][BAD]['state']=='QUARANTINED'
    if status==200:assert cp['terminal'][BAD]['history'][0]['state']=='HTTP_CAPTURED_NOT_COMMITTED'
    before=list(calls)
    settings={**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':result['input_sha256']}
    child=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':settings})
    resumed=run(child)
    assert resumed['status']=='succeeded' and resumed['sql_committed_this_job']==0
    assert calls==before and len(backend.writes)==1 and len(backend.matches)==1


@pytest.mark.parametrize('reason',['SQL_COMMIT_NOT_PROVEN','SQL_VALUE_CONSTRAINT','SOURCE_NAMESPACE_CHANGED'])
def test_sql_valueerror_is_never_misclassified_as_parser_error(reason):
    backend=Backend()
    def fail(*a):raise ValueError(reason)
    backend.persist=fail
    run,_,_,calls,_=execution(backend=backend)
    result=run()
    assert result['status']=='partial' and result['termination_reason']==reason
    assert result['sql_committed']==0 and not backend.matches
    assert len(result['quarantined'])==1  # The unrelated first semantic conflict only.
    assert result['quarantined'][0]['url']==BAD


@pytest.mark.parametrize('kind',['parse','identity'])
def test_mass_parser_errors_stop_source_not_unbounded_quarantine(kind):
    run,job,backend,calls,_=execution(bad_count=12,bad_body=b'<html><body>Broken layout without product</body></html>' if kind=='parse' else None)
    result=run()
    assert result['status']=='partial' and result['termination_reason']=='SOURCE_CARD_ERROR_THRESHOLD'
    assert len(result['quarantined'])==(job.settings_snapshot['rate_settings']['consecutive_error_threshold'] if kind=='parse' else result['safety_gate_size'])
    assert result['remaining']>0 and not backend.writes and not backend.matches


def test_real_59_committed_and_four_quarantines_are_not_mass_source_failure():
    from universal_supplier.control_plane.source_runtime import mass_identity_failure
    saved=json.loads(gzip.decompress((FIX.parent/'beka_tr_59_checkpoint.json.gz').read_bytes()))
    rows={u:r for u,r in saved['checkpoint']['terminal'].items() if r['state']=='COMMITTED'}
    for n in range(4):rows[str(n)]={'state':'QUARANTINED','receipt':{'isolation':{'category':'identity'}}}
    assert len(rows)==63 and not mass_identity_failure(rows,5)
    for n in range(60):rows['bad'+str(n)]={'state':'QUARANTINED','receipt':{'isolation':{'category':'identity'}}}
    assert mass_identity_failure(rows,5)


def test_actual_three_followup_variants_remain_semantic_quarantine():
    from universal_supplier.control_plane.source_jobs import parse_existing
    rows=json.loads(gzip.decompress((FIX.parent/'semantic_followup_pages.json.gz').read_bytes()))
    assert len(rows)==3
    for row in rows:
        body=base64.b64decode(row['body_base64'])
        assert hashlib.sha256(body).hexdigest()==row['sha256']
        with pytest.raises(ValueError,match='title, name and SKU disagree'):
            parse_existing('beka_mak_tr',body.decode('utf-8'),row['url'])


def test_quarantine_receipt_needs_capture_and_absence_of_sql():
    run,job,_,_,_=execution();result=run();receipt=result['quarantined'][0]
    entry=job.settings_snapshot['source_input']['entries'][0]
    def classify_one(r,sql):
        return classify([entry],[r],lambda *a:sql,source='beka_mak_tr',parent_job_id=1,manifest_sha256=result['input_sha256'])
    valid=classify_one(receipt,'NOT_FOUND')
    assert valid[0]['state']=='QUARANTINED'
    plan=pending_manifest({'supplier_code':'beka_mak_tr','entries':[entry]},valid,parent_job_id=1,parent_manifest_sha256=result['input_sha256'])
    assert plan['automatic_resume_allowed'] and not plan['http_pending']['entries'] and not plan['offline_pending']
    for sql in ('UNKNOWN','COMMITTED'):assert classify_one(receipt,sql)[0]['state']=='UNCERTAIN'
    broken=deepcopy(receipt);broken.pop('original_capture_metadata')
    assert classify_one(broken,'NOT_FOUND')[0]['state']=='UNCERTAIN'


def test_sql_probe_types_null_external_id_for_archived_preparse_capture():
    from types import SimpleNamespace
    from universal_supplier.control_plane.source_backend import SourceBackend
    run,job,_,_,_=execution();result=run();receipt={**result['quarantined'][0],'external_id':None}
    queries=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,args):queries.append((query,args))
        def fetchall(self):return []
    backend=SourceBackend.__new__(SourceBackend);backend.code='beka_mak_tr'
    backend.repo=SimpleNamespace(connection=SimpleNamespace(cursor=Cursor))
    assert backend.probe(job.settings_snapshot['source_input']['entries'][0],receipt)=='NOT_FOUND'
    query,args=queries[0]
    assert 'OR %s::text IS NULL' in query and args[-2:]==(None,None)


@pytest.mark.parametrize('status,probe,reason,rows,expected',[
    ('IDLE','NOT_FOUND','SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',[(7188,57914,'146135')],'rejected'),
    ('IDLE','NOT_FOUND','SOURCE_UPDATE_SEMANTIC_CONFLICT:AMBIGUOUS_SAVED_BRAND',[(7188,57914,'146135')],'rejected'),
    ('INERROR','NOT_FOUND','SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',[(7188,57914,'146135')],'ROLLBACK_NOT_PROVEN'),
    ('IDLE','UNKNOWN','SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',[(7188,57914,'146135')],'SQL_NOT_PROVEN'),
    ('IDLE','COMMITTED','SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',[(7188,57914,'146135')],'SQL_NOT_PROVEN'),
    ('IDLE','NOT_FOUND','SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',[],'MAPPING_NOT_PROVEN'),
    ('IDLE','NOT_FOUND','SOURCE_UPDATE_MAPPING_CONFLICT',[(7188,57914,'146135')],'SOURCE_UPDATE_MAPPING_CONFLICT'),
])
def test_actual_kami_link_rejection_only_isolates_proven_rollback(status,probe,reason,rows,expected):
    from types import SimpleNamespace
    from psycopg.pq import TransactionStatus
    from universal_supplier.control_plane.source_backend import SourceBackend,SourceUpdateRejected
    from universal_supplier.control_plane.source_runtime import restore_capture
    from universal_supplier.control_plane.source_jobs import parse_existing
    saved=json.loads(gzip.decompress((FIX.parent/'kami_linked_rejection.json.gz').read_bytes()))
    receipt=saved['receipt'];capture=restore_capture(receipt['original_capture_metadata'])
    body=base64.b64decode(saved['body_base64']);assert hashlib.sha256(body).hexdigest()==capture.evidence_sha256
    card=parse_existing('kami',body.decode('utf-8'),capture.final_url)
    assert card.external_id=='variant:9833201'
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,args):assert args==('kami',card.external_id)
        def fetchall(self):return rows
    def guarded(*a,**kw):raise RuntimeError(reason)
    connection=SimpleNamespace(info=SimpleNamespace(transaction_status=getattr(TransactionStatus,status)),cursor=Cursor)
    backend=SourceBackend.__new__(SourceBackend);backend.code='kami';backend.run_id=33
    backend.repo=SimpleNamespace(connection=connection,persist_kami_observation=guarded)
    backend.probe=lambda e,r:probe
    if expected=='rejected':
        with pytest.raises(SourceUpdateRejected) as error:backend.persist(card,capture)
        assert error.value.proof=={'rollback_verified':True,'sql_absent':True,'source_product_id':7188,'catalog_product_id':57914,'sterbrust_product_id':'146135'}
    else:
        with pytest.raises(RuntimeError,match=expected) as error:backend.persist(card,capture)
        assert not isinstance(error.value,SourceUpdateRejected)


def test_native_runtime_preserves_link_by_quarantining_rejected_update():
    from universal_supplier.control_plane.source_backend import SourceUpdateRejected
    backend=Backend()
    def reject(*a):raise SourceUpdateRejected('SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT',
        {'rollback_verified':True,'sql_absent':True,'source_product_id':7188,'catalog_product_id':57914,'sterbrust_product_id':'146135'})
    backend.persist=reject
    run,job,backend,calls,url=setup('kami',backend=backend)
    result=run()
    assert result['status']=='succeeded' and not result['collection_complete'] and result['sql_committed']==0,result
    assert len(result['quarantined'])==1 and result['quarantined'][0]['isolation']['category']=='matching'
    assert result['quarantined'][0]['isolation']['rejection_proof']['sterbrust_product_id']=='146135'
    assert not backend.writes and not backend.matches and result['termination_reason'] is None


def test_final_unknown_quarantine_sql_probe_is_global_stop():
    backend=Backend();backend.probe=lambda *a:'UNKNOWN'
    run,job,backend,calls,url=execution(backend=backend)
    snapshot=deepcopy(job.settings_snapshot)
    snapshot['source_input']['entries']=[e for e in snapshot['source_input']['entries'] if e['url']==BAD]
    result=run(replace(job,settings_snapshot=snapshot))
    assert result['status']=='partial' and result['resume_uncertain']
    assert result['termination_reason'] in ('SQL_COMMIT_NOT_PROVEN','UNCERTAIN_OR_FAILED_RESUME_REQUIRES_OPERATOR')


def test_bounded_continuation_cannot_extend_scope_sha_or_attempts(monkeypatch):
    env={'scope_sha256':'scope','attempt':4,'id':'batch'};previous={'source_batch':env}
    scopes=['scope']+[str(n) for n in range(21)]
    value={'batch_id':'batch','scope_hashes':scopes,'continuation':{'format':'live11-continuation/v1',
        'batch_id':'batch','phases':{scope:{'max_attempt':8} for scope in scopes},'historical_envelopes':[digest(env)],
        'manual_hashes':{'review_decisions':[],'candidate_rejections':[]},'mappings1282':[[n] for n in range(1282)]}}
    sha='b'*64;monkeypatch.setattr(recovery,'approval',lambda:(value,sha));monkeypatch.setenv(recovery.ENV+'_SHA256',sha)
    proof={'kind':'continuation','parent_job_id':51,'approval_sha256':sha}
    recovery.continuation_envelope_check(value,env)
    for attempt in range(4,8):
        previous['source_batch']['attempt']=attempt
        child=recovery.continuation_envelope(previous,attempt,attempt,proof)
        assert child['attempt']==attempt+1
        recovery.continuation_envelope_check(value,child)
    previous['source_batch']['attempt']=8
    with pytest.raises(PermissionError,match='EXHAUSTED'):recovery.continuation_envelope(previous,8,8,proof)
    child['continuation_recovery']['approval_sha256']='wrong'
    with pytest.raises(PermissionError):recovery.continuation_envelope_check(value,child)
    with pytest.raises(PermissionError):recovery.continuation_rule(value,'unknown-scope')


@pytest.mark.parametrize('followups',[False,True])
def test_actual_59_committed_plus_failed_capture_recovery_no_http_ingest(monkeypatch,tmp_path,followups):
    from offline_fixture_clock import freeze_runtime_utc
    freeze_runtime_utc(monkeypatch)
    from universal_supplier.control_plane.queue import CrawlJob
    from test_partner_pilot import CFG
    from test_source_jobs import deny
    saved=json.loads(gzip.decompress((FIX.parent/'beka_tr_59_checkpoint.json.gz').read_bytes()))
    old=json.loads(gzip.decompress((FIX.parent/'post_stop_41_50.json.gz').read_bytes()))
    value=old['manifest'];sha='b'*64;cp=saved['checkpoint'];original=saved['input'];url=saved['failed_url']
    entry={'source':'beka_mak_tr','owner_job_id':41,'input_sha256':cp['manifest_sha256'],
        'url':url,'failed_row':cp['terminal'][url],'captured_receipt':saved['captured_receipt']}
    value['continuation']={'quarantine':[entry]}
    monkeypatch.setattr(recovery,'approval',lambda:(value,sha))
    monkeypatch.setenv(recovery.ENV+'_FILE','offline-fixture-confirmed')
    monkeypatch.setattr(batch,'validate_snapshot',lambda *a,**kw:None)
    folder=Path(__import__('os').environ['SOURCE_RUNNER_EVIDENCE_ROOT'])/'beka_mak_tr/job-41'
    folder.mkdir(parents=True)
    (folder/'checkpoint.json').write_text(json.dumps(cp,ensure_ascii=False),encoding='utf-8')
    (folder/'input.json').write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
    # Archived absolute path is an evidence reference; packaged body verifies it offline.
    read=Path.read_bytes;archived=saved['captured_receipt']['original_capture_metadata']['evidence_ref']
    body=gzip.decompress(FIX.read_bytes());monkeypatch.setattr(Path,'read_bytes',lambda p:body if str(p)==archived else read(p))
    overrides=recovery.continuation_receipts(value,'beka_mak_tr',41,cp['manifest_sha256'],cp)
    assert len(overrides)==1 and overrides[url]['state']=='QUARANTINED' and cp==saved['checkpoint']
    changed=deepcopy(cp);changed['terminal'][url]['receipt']['reason']='changed'
    with pytest.raises(PermissionError):recovery.continuation_receipts(value,'beka_mak_tr',41,cp['manifest_sha256'],changed)
    backend=Backend();fps={r['receipt']['capture_fingerprint'] for r in cp['terminal'].values() if r['state']=='COMMITTED'}
    backend.probe=lambda e,r:'COMMITTED' if r and r.get('capture_fingerprint') in fps else 'NOT_FOUND' if r else 'UNKNOWN'
    snapshot=deepcopy(value['post_stop']['parents']['41']['snapshot'])
    proof={'kind':'continuation','parent_job_id':51,'owner_job_id':41,'run_id':25,'approval_sha256':sha}
    snapshot['source_batch']['recovery']=proof
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=41,resume_input_sha256=cp['manifest_sha256'])
    monkeypatch.setattr(recovery,'runtime_proof',lambda *a:proof)
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({r['APPROVAL_HOST']:r['APPROVAL_DIGEST_SHA256'] for r in batch.matrix_rows()}))
    job=CrawlJob(61,snapshot['source_input']['canary']['supplier_id'],'beka_mak_tr','beka_mak_tr',
        'https://www.bekamak.com','manual','full','running',None,snapshot)
    # Stop immediately AFTER native reconciliation, before robots/new URL GET.
    def stop_before_http(*a,**kw):
        raise AssertionError('UNEXPECTED_HTTP')
    captures={row['url']:base64.b64decode(row['body_base64']) for row in
        json.loads(gzip.decompress((FIX.parent/'semantic_followup_pages.json.gz').read_bytes()))}
    requested=[]
    class FollowupClient:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url)
            if url.endswith('robots.txt'):return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /')
            assert url in captures,'Unexpected followup outside actual saved fixture'
            requested.append(url)
            if len(requested)==3:(folder.parent/'job-61/STOP_REQUESTED').write_text('OPERATOR_FIXTURE_REPLAY_DONE')
            return HttpResponse(url,200,'text/html; charset=utf-8',captures[url])
    from universal_supplier.control_plane import source_runtime as runtime
    original_classify=runtime.classify
    def reconciled(*args,**kwargs):
        rows=original_classify(*args,**kwargs)
        stop=folder.parent/'job-61/STOP_REQUESTED';stop.parent.mkdir(parents=True,exist_ok=True)
        if not followups:stop.write_text('OPERATOR_FIXTURE_STOP_BEFORE_HTTP')
        return rows
    monkeypatch.setattr(runtime,'classify',reconciled)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=FollowupClient if followups else stop_before_http,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['sql_committed']==59 and result['sql_committed_this_job']==0,result
    assert result['request_metrics']['requests_total']==(4 if followups else 0) and not backend.writes and not backend.matches
    assert result['termination_reason'].startswith('OPERATOR_FIXTURE') and len(requested)==(3 if followups else 0),result
    after=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert after['terminal'][url]['state']=='QUARANTINED' and len(after['frontier'])==(15 if followups else 18)
    assert sum(row['state']=='QUARANTINED' for row in after['terminal'].values())==(4 if followups else 1)
    for u,row in cp['terminal'].items():
        assert after['terminal'][u]['history'][:len(row['history'])]==row['history']
        if row['state']=='COMMITTED':assert after['terminal'][u]['receipt']==row['receipt']


def test_report_does_not_present_quarantine_as_full_data_completion():
    row={'id':1,'status':'succeeded','settings_snapshot':{'supplier_code':'beka_mak_tr',
        'source_batch':{'phase':'REFRESH'}},'metadata':{'source_runtime':{'quarantined':[{'url':BAD}],
        'sql_committed':59,'processed':78,'remaining':0}}}
    report=batch.report([row]);phase=next(p for p in report['phases'] if p['phase']=='REFRESH')
    assert phase['execution_finished'] and phase['state']=='завершено с карантином'
    assert phase['quarantined_count']==1 and phase['operator_action_required'] and not report['complete']


def test_continuation_admission_checks_target_manual_mappings_owner_and_checkpoint(monkeypatch,tmp_path):
    from test_live11_post_stop import Cursor
    from test_partner_pilot import CFG
    from universal_supplier.control_plane import source_runtime,source_controls
    saved=json.loads(gzip.decompress((FIX.parent/'beka_tr_59_checkpoint.json.gz').read_bytes()))
    value=json.loads(gzip.decompress((FIX.parent/'post_stop_41_50.json.gz').read_bytes()))['manifest']
    item=deepcopy(value['post_stop']['parents']['41']);item.update(id=51,run_id=25)
    original=saved['input'];runtime={'owner_job_id':41,'input_sha256':digest(original),'status':'partial','resume_uncertain':[BAD]}
    path=source_runtime.job_dir('beka_mak_tr',41)/'checkpoint.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved['checkpoint']),encoding='utf-8')
    initial={k:item[k] for k in ('id','supplier_id','status','run_id','source_hashes')}
    initial.update(checkpoint_sha256=recovery.file_sha(path),runtime_sha256=digest(runtime))
    value['continuation']={'format':'live11-continuation/v1','batch_id':value['batch_id'],
        'phases':{e['scope_sha256']:{'max_attempt':8} for e in value['envelopes']},
        'manual_hashes':value['post_stop']['manual_hashes'],'mappings1282':value['post_stop']['mappings1282'],
        'quarantine':[{'owner_job_id':41,'url':BAD}]}
    value['continuation']['phases'][item['snapshot']['source_batch']['scope_sha256']]['initial_parent']=initial
    monkeypatch.setattr(batch,'validate_snapshot',lambda *a,**kw:None)
    monkeypatch.setattr(source_runtime,'read_input',lambda *a:original)
    checked=[];monkeypatch.setattr(source_controls,'verify_resume',lambda *a:checked.append(a))
    q=Cursor(item,value);q.runtime=runtime
    proof=recovery.validate_continuation(q,item['snapshot'],51,CFG,True,value,'b'*64)
    assert proof['kind']=='continuation' and proof['parent_job_id']==51 and proof['owner_job_id']==41 and checked
    recovery.admission_runtime(runtime,proof)
    unknown={**runtime,'resume_uncertain':['unknown']}
    with pytest.raises(PermissionError):recovery.admission_runtime(unknown,proof)
    for query in ('pg_control_system','FROM review_decisions','sp.id,s.code,sp.external_id,sp.catalog_product_id','WHERE t.supplier_id'):
        q.changed=query
        with pytest.raises(PermissionError):recovery.validate_continuation(q,item['snapshot'],51,CFG,True,value,'b'*64)
    q.changed=None
    altered=deepcopy(saved['checkpoint']);altered['revision']+=1
    path.write_text(json.dumps(altered),encoding='utf-8')
    with pytest.raises(PermissionError,match='CHECKPOINT'):recovery.validate_continuation(q,item['snapshot'],51,CFG,True,value,'b'*64)


@pytest.mark.parametrize('boundary',['quarantine','budget','systemic'])
def test_existing_session_continues_isolated_urls_and_restores_on_global_stop(real,monkeypatch,tmp_path,boundary):
    session,manifest,db=session_setup(real);manifest['continuation']={'enabled_for_offline_test':True}
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(recovery,'approval',lambda:(manifest,real[2]))
    monkeypatch.setattr(recovery,'recover',lambda *a:{'resumed_partial':[61]})
    monkeypatch.setattr(recovery,'file_sha',lambda *a:real[2])
    monkeypatch.setenv(recovery.ENV+'_FILE','offline')
    monkeypatch.setattr(session.time,'sleep',lambda *a:None)
    closed=[];stopped=[];continued=[];states=[]
    class Worker:
        def __init__(self,*a,**kw):
            assert kw['max_parallel_supplier_jobs']==2
            self.pool=self
        def tick(self):pass
        def shutdown(self,wait):closed.append(wait)
    monkeypatch.setattr(session,'Orchestrator',Worker)
    phases=[{'source':code,'phase':phase,'job_id':100+i*2+j,'state':'завершено',
        'execution_finished':True,'quarantined_count':0} for i,code in enumerate(batch.SOURCE_ORDER)
        for j,phase in enumerate(('REFRESH','DISCOVERY'))]
    phases[2].update(state='завершено с карантином',quarantined_count=1)
    if boundary!='quarantine':
        pending=deepcopy(phases);pending[3].update(state='требуется решение оператора',execution_finished=False)
        states.append({'complete':False,'beka_gate_released':True,'phases':pending})
    states.append({'complete':False,'beka_gate_released':True,'phases':phases})
    db.source_batch_status=lambda *a:states.pop(0)
    db.stop_source_batch=lambda *a:stopped.append(True)
    def continuation(*a):
        continued.append(True)
        if boundary=='systemic':raise PermissionError('RECOVERY_SYSTEMIC_FAILURE_REQUIRES_OPERATOR')
    monkeypatch.setattr(recovery,'recover_continuation',continuation)
    if boundary=='systemic':
        with pytest.raises(PermissionError,match='SYSTEMIC'):session.run(db)
        assert stopped==[True]
    else:
        result=session.run(db)
        assert result['reason']=='COMPLETED_WITH_QUARANTINE' and not stopped
    assert db.settings==manifest['settings_before'] and closed==[True]
    assert bool(continued)==(boundary!='quarantine')
