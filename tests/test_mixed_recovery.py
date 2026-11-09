"""Offline guards for the signed mixed-recovery admission; SQL and HTTP forbidden."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib,json
import pytest
from universal_supplier.control_plane import commercial_continuation as cc,automation_execution as ae
from universal_supplier.control_plane.source_resume import SourceCheckpoint,digest,pending_manifest

ENTRIES=[{'source_product_id':i,'source_external_id':f'x{i}','url':f'https://intervesp.ru/p/{i}'} for i in range(1,6)]
URLS=[e['url'] for e in ENTRIES]
ENGINE='e'*64;MANIFEST='0'*64;JID=171;RUN=62


def receipt(url,state,*,external='x',fp=None,matching=False,capture=True,manifest=MANIFEST):
    r={'url':url,'state':state,'external_id':external,'capture_fingerprint':fp or hashlib.sha256(url.encode()).hexdigest(),
        'origin':{'source':'intervesp','job_id':JID,'manifest_sha256':manifest}}
    if capture:r['original_capture_metadata']={'requested_url':url,'final_url':url,'http_status':200,
        'redirects':None,'observed_at':'2026-11-06T08:04:06+00:00','response_sha256':'r'*64,'evidence_sha256':'v'*64,
        'evidence_ref':f'captures/{fp or "c"}.bin'}
    if state=='COMMITTED':
        r['sql_verified']=True
        r['matching_receipt']={'status':'succeeded','job_id':JID,'run_id':RUN,'external_id':external} if matching else None
    return r


def evidence(tmp_path,change=None):
    transport_sha=hashlib.sha256(b'transport').hexdigest()
    (tmp_path/'transport-errors').mkdir(exist_ok=True)
    (tmp_path/'transport-errors'/(transport_sha+'.json')).write_bytes(b'transport')
    original={'format':'source-runtime-input/v1','supplier_code':'intervesp','mode':'REFRESH','entries':deepcopy(ENTRIES),
        'engine_sha256':ENGINE,'rate_policy':{}}
    manifest=digest(original)
    cp={'format':'source-discovery-checkpoint/v1','source':'intervesp','job_id':JID,'manifest_sha256':manifest,
        'revision':1,'frontier':[URLS[4]],'inflight':{},'terminal':{
            URLS[0]:{'state':'COMMITTED','receipt':receipt(URLS[0],'COMMITTED',external='x1',matching=True,manifest=manifest)},
            URLS[1]:{'state':'COMMITTED','receipt':receipt(URLS[1],'COMMITTED',external='x2',matching=True,manifest=manifest)},
            URLS[2]:{'state':'HTTP_CAPTURED_NOT_COMMITTED','receipt':receipt(URLS[2],'HTTP_CAPTURED_NOT_COMMITTED',external='x3',manifest=manifest)},
            URLS[3]:{'state':'UNCERTAIN','receipt':receipt(URLS[3],'UNCERTAIN',capture=False,manifest=manifest)}},
        'visited':[],'discovery_receipts':{},'discovery_finished':False,'category_failures':[]}
    result={'owner_job_id':JID,'crawl_run_id':RUN,'input_sha256':manifest,'sql_committed':2,'processed':4,
        'status':'partial','stop_scope':'system','termination_reason':'AUTOMATION_UNKNOWN_HTTP_OUTCOME'}
    runtime={'input_sha256':manifest,'termination_reason':'AUTOMATION_UNKNOWN_HTTP_OUTCOME',
        'sql_committed':2,'processed':4,'checkpoint':str(tmp_path/'checkpoint.json')}
    block={'format':ae.MIXED_RECOVERY_KIND,'source':'intervesp','host':'intervesp.ru','parent_job_id':JID,
        'parent_manifest_sha256':manifest,'parent_approval_sha256':'2'*64,'parent_engine_sha256':ENGINE,
        'requires_operator_confirmation':True,
        'reauthorized_uncertain':{URLS[3]:{'transport_response_available':False,'transport_evidence_sha256':transport_sha}}}
    value={'original':original,'cp':cp,'result':result,'runtime':runtime,'block':block}
    if change:change(value)
    cp2=deepcopy(value['cp']);cp2['checksum']=digest({k:v for k,v in cp2.items() if k!='checksum'})
    for name,data in (('input',value['original']),('checkpoint',cp2),('result',value['result'])):
        (tmp_path/(name+'.json')).write_text(json.dumps(data),encoding='utf-8')
    return value


def check(tmp_path,value):
    return cc.check_mixed_recovery(tmp_path,'intervesp',JID,RUN,'partial',value['runtime'],
        [{'source_product_id':e['source_product_id'],'source_external_id':e['source_external_id'],'url':e['url']} for e in value['original']['entries']],
        ENGINE,value['block'],verify=lambda *a:None)


def test_mixed_checkpoint_accounted_per_state(tmp_path):
    v=evidence(tmp_path);r=check(tmp_path,v)
    assert (r['committed'],r['not_requested'],r['recover_pending'])==(2,1,2)
    def pending(v):v['cp']['terminal'][URLS[0]]['receipt']['matching_receipt']=None
    r=evidence(tmp_path,pending);r=check(tmp_path,r)
    assert r['matching_pending']==1
    assert r['offline_pending']==[URLS[2]] and r['reauthorized_uncertain']==[URLS[3]] and len(r['receipts'])==2


def test_uncertain_without_signed_reauthorization_rejects(tmp_path):
    def change(v):v['block']['reauthorized_uncertain']={}
    with pytest.raises(PermissionError,match='MIXED_UNCERTAIN_NOT_REAUTHORIZED'):check(tmp_path,evidence(tmp_path,change))


def test_missing_transport_evidence_rejects(tmp_path):
    def change(v):v['block']['reauthorized_uncertain'][URLS[3]]['transport_evidence_sha256']='0'*64
    with pytest.raises(PermissionError,match='MIXED_UNCERTAIN_NOT_REAUTHORIZED'):check(tmp_path,evidence(tmp_path,change))


def test_weak_commit_or_scope_drift_rejects(tmp_path):
    def commit(v):v['cp']['terminal'][URLS[0]]['receipt']['matching_receipt']={'status':'succeeded','job_id':JID,'run_id':999,'external_id':'x1'}
    with pytest.raises(PermissionError,match='MIXED_COMMIT_NOT_DURABLE'):check(tmp_path,evidence(tmp_path,commit))
    def unverified(v):v['cp']['terminal'][URLS[0]]['receipt']['sql_verified']=False
    with pytest.raises(PermissionError,match='MIXED_COMMIT_NOT_DURABLE'):check(tmp_path,evidence(tmp_path,unverified))
    def counts(v):v['result']['sql_committed']=1
    with pytest.raises(PermissionError,match='MIXED_RESULT_COUNTS_CHANGED'):check(tmp_path,evidence(tmp_path,counts))
    def binding(v):v['block']['parent_job_id']=999
    with pytest.raises(PermissionError,match='MIXED_RECOVERY_BINDING_CHANGED'):check(tmp_path,evidence(tmp_path,binding))
    def inflight(v):v['cp']['inflight'][URLS[4]]={}
    with pytest.raises(PermissionError,match='MIXED_RECOVERY_STATE_NOT_SAFE'):check(tmp_path,evidence(tmp_path,inflight))


def test_reauthorized_uncertain_moves_to_http_pending():
    origin={'source':'intervesp','job_id':JID,'manifest_sha256':MANIFEST}
    rows=[{'entry':ENTRIES[3],'receipt':{'url':URLS[3],'state':'UNCERTAIN','origin':dict(origin)},'state':'UNCERTAIN','origin':dict(origin)},
          {'entry':ENTRIES[4],'receipt':{'url':URLS[4],'state':'NOT_REQUESTED','origin':dict(origin)},'state':'NOT_REQUESTED','origin':dict(origin)}]
    blocked=pending_manifest({'supplier_code':'intervesp','entries':[ENTRIES[3],ENTRIES[4]]},rows,parent_job_id=JID,parent_manifest_sha256=MANIFEST)
    assert not blocked['automatic_resume_allowed'] and blocked['uncertain']==[URLS[3]]
    allowed=pending_manifest({'supplier_code':'intervesp','entries':[ENTRIES[3],ENTRIES[4]]},rows,parent_job_id=JID,
        parent_manifest_sha256=MANIFEST,reauthorized_uncertain={URLS[3]})
    assert allowed['automatic_resume_allowed'] and {e['url'] for e in allowed['http_pending']['entries']}==set(URLS[3:5])


def session(block=None):
    rule={'host':'intervesp.ru'}
    if block is not None:rule['mixed_recovery']=block
    return SimpleNamespace(sha='s'*64,value={'suppliers':{'intervesp':rule}})


def test_reauthorization_is_session_and_parent_bound(tmp_path,monkeypatch):
    v=evidence(tmp_path);block=v['block']
    sha=block['parent_manifest_sha256']
    monkeypatch.setattr(ae,'ACTIVE',session(block))
    assert ae.mixed_recovery('intervesp',JID,sha) is block
    assert ae.reauthorized_uncertain('intervesp',JID,sha)=={URLS[3]}
    assert ae.reauthorized_uncertain('intervesp',999,sha)==set()
    monkeypatch.setattr(ae,'ACTIVE',session())
    assert ae.reauthorized_uncertain('intervesp',JID,sha)==set()
    monkeypatch.setattr(ae,'ACTIVE',None)
    assert ae.reauthorized_uncertain('intervesp',JID,sha)==set()


def test_checkpoint_reauthorization_preserves_history(tmp_path):
    cp=SourceCheckpoint(tmp_path,'intervesp',JID,MANIFEST,validator=lambda u:None,lease_acquired=True)
    cp.add_urls(URLS);cp.claim_batch(5)
    cp.record_terminal([{'url':URLS[3],'state':'UNCERTAIN','origin':{'source':'intervesp','job_id':JID,'manifest_sha256':MANIFEST}}])
    cp.reauthorize_uncertain([URLS[3]])
    assert URLS[3] in cp.state['frontier'] and cp.state['terminal'][URLS[3]]['state']=='NOT_REQUESTED'
    assert cp.state['terminal'][URLS[3]]['history'][-1]['operator_reauthorization'] is True
    with pytest.raises(ValueError,match='REAUTHORIZED_URL_NOT_UNCERTAIN'):
        cp.reauthorize_uncertain([URLS[3]])


def recover_contract(tmp_path,monkeypatch,change=None):
    from tests.test_automation_execution import grant,write,mocked_identity
    value=grant()
    block={'format':ae.MIXED_RECOVERY_KIND,'source':'intervesp','host':value['suppliers']['intervesp']['host'],
        'parent_job_id':171,'parent_manifest_sha256':'0'*64,'parent_approval_sha256':'2'*64,'parent_engine_sha256':'1'*64,
        'requires_operator_confirmation':True,'reauthorized_uncertain':{'https://intervesp.ru/p/4':{'transport_response_available':False}}}
    if change:change(block)
    value['suppliers']['intervesp']['mixed_recovery']=block
    path,sha=write(tmp_path,value)
    mocked_identity(monkeypatch)
    ae.load(path,sha)
    return block


def test_approval_validates_mixed_recovery_contract(tmp_path,monkeypatch):
    recover_contract(tmp_path,monkeypatch)


@pytest.mark.parametrize('change,reason',[
    (lambda b:b.update(requires_operator_confirmation=False),'CONTRACT_INVALID'),
    (lambda b:b.update(parent_job_id=0),'PARENT_INVALID'),
    (lambda b:b.update(parent_manifest_sha256='short'),'MANIFEST_INVALID'),
    (lambda b:b.update(parent_approval_sha256='short'),'APPROVAL_INVALID'),
    (lambda b:b.update(parent_engine_sha256='short'),'ENGINE_INVALID'),
    (lambda b:b.update(reauthorized_uncertain={}),'URLS_INVALID'),
    (lambda b:b['reauthorized_uncertain'].update(u={'transport_response_available':True}),'URLS_INVALID'),
])
def test_invalid_mixed_recovery_contract_rejects(tmp_path,monkeypatch,change,reason):
    from tests.test_automation_execution import grant,write,mocked_identity
    value=grant()
    block={'format':ae.MIXED_RECOVERY_KIND,'source':'intervesp','host':value['suppliers']['intervesp']['host'],
        'parent_job_id':171,'parent_manifest_sha256':'0'*64,'parent_approval_sha256':'2'*64,'parent_engine_sha256':'1'*64,
        'requires_operator_confirmation':True,'reauthorized_uncertain':{'u':{'transport_response_available':False}}}
    change(block);value['suppliers']['intervesp']['mixed_recovery']=block
    path,sha=write(tmp_path,value)
    mocked_identity(monkeypatch)
    with pytest.raises(PermissionError,match='AUTOMATION_MIXED_RECOVERY_'+reason):
        ae.load(path,sha)


def recover_value(tmp_path):
    v=evidence(tmp_path)
    old={'scope_sha256':'s','target':{'pgdata':'x'},'mappings1282':[[1,2]],'protected_live11_batch_id':'b',
        'not_before':'2026-11-06T07:50:00Z','execution_until':'2026-11-06T11:50:00Z',
        'suppliers':{'intervesp':{'policy':{'min_request_interval_seconds':5.0},'targets':[1],
            'host':'intervesp.ru','supplier_id':13,'limits':{'REFRESH':5,'DISCOVERY':0}}},
        'host_policies':{'intervesp.ru':{'intervesp':'id5'}}}
    path=tmp_path/'old.json';path.write_text(json.dumps(old),encoding='utf-8')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    p={'kind':'commercial-receipt-continuation/v1','previous_path':str(path),'previous_approval_sha256':sha,
        'protected_jobs':[[9,'x']],'jobs':[{'source':'intervesp','disposition':'RECOVER_RESUME'}]}
    value=deepcopy(old);value['continuation']=p;value['operation_id']=sha;value['protected_jobs']=[[9,'x']]
    value['suppliers']['intervesp']['mixed_recovery']=v['block']
    return value,p


def test_recover_source_may_add_only_the_signed_block(tmp_path,monkeypatch):
    value,p=recover_value(tmp_path)
    monkeypatch.setattr(cc,'validate_window',lambda *a,**k:None)
    monkeypatch.setattr(cc,'receipt_proof',lambda *a,**k:p)
    assert cc.validate_live(None,value) is None
    value['suppliers']['intervesp']['policy']={'min_request_interval_seconds':1.0}
    with pytest.raises(PermissionError,match='FROZEN_SOURCE_CONTRACT_CHANGED'):
        cc.validate_live(None,value)


def test_tick_resumes_whitelisted_parent_instead_of_failing_or_requeueing(tmp_path,monkeypatch):
    import universal_supplier.control_plane.commercial_refresh as cr
    from universal_supplier.control_plane.scheduler import Scheduler
    value,p=recover_value(tmp_path)
    value['suppliers']['intervesp'].update(supplier_id=13,targets=[{'source_product_id':1}])
    url=URLS[3]
    job=[JID,'partial',RUN,{'automation':{'approval_sha256':'f20f2142'}},
         {'source_runtime':{'owner_job_id':JID,'input_sha256':MANIFEST,'termination_reason':'AUTOMATION_UNKNOWN_HTTP_OUTCOME',
            'stop_scope':'system','resume_uncertain':[url],'resume_available':False}},'err']
    calls=[]
    class DB:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self
        def execute(self,sql,args=()):
            assert sql.lstrip().startswith('SELECT')
            self.row=job if ('%s ORDER BY j.id DESC' in sql and 'WHERE j.id=%s' not in sql) else None
        def fetchone(self):return self.row
    db=DB();s=SimpleNamespace(value=value,sha='n'*64,check=lambda:None,source_reason=lambda code:None,
        fail=lambda r:(_ for _ in ()).throw(AssertionError('session must not fail')))
    scheduler=Scheduler(None,db,automation=s);scheduler._connect=lambda:db
    scheduler.queue=SimpleNamespace(source_resume_status=lambda jid:True,
        enqueue=lambda sid,**kw:calls.append(kw) or SimpleNamespace(enqueued=True,job_id=172))
    monkeypatch.setattr('universal_supplier.control_plane.automation_execution.reauthorized_uncertain',
        lambda code,owner,sha:{url} if owner==JID else set())
    assert cr.tick(scheduler,None)==[172]
    assert calls[0]['source_mode']=='RESUME' and calls[0]['resume_job_id']==JID
    assert not scheduler.blocked
