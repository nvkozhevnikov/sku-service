"""Actual KAMI stop checkpoint, all transport/SQL mocked or denied."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import gzip,hashlib,json,os
import pytest
from test_source_runtime import isolation,Backend,setup
from test_host_fetch import Clock
from test_partner_pilot import CFG
from universal_supplier.control_plane import source_recovery as recovery,source_runtime as runtime,source_batch as batch
from universal_supplier.control_plane.source_resume import classify,pending_manifest,digest
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.http_capture import HttpResponse

@pytest.fixture
def saved(tmp_path,monkeypatch):
    from offline_fixture_clock import freeze_runtime_utc
    freeze_runtime_utc(monkeypatch)
    data=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/kami_scope_stop.json.gz').read_bytes()))
    auth=tmp_path/'operator.json';auth.write_text(json.dumps(data['authorization']),encoding='utf-8')
    data['item']['decision'].update(authorization_path=str(auth),authorization_sha256=hashlib.sha256(auth.read_bytes()).hexdigest())
    value={'batch_id':data['item']['decision']['batch_id'],'continuation':{'quarantine':[data['item']]},
        'compatibility':{'base_engine':data['input']['engine_sha256']},'input_hashes':[digest(data['input'])]}
    monkeypatch.setattr(recovery,'approval',lambda:(value,'b'*64))
    # Queue/scope/HEAD admission is covered by the real recovery regression;
    # these runtime tests isolate receipt reconciliation using saved inputs.
    monkeypatch.setattr(batch,'validate_snapshot',lambda *a,**kw:None)
    monkeypatch.setenv(recovery.ENV+'_FILE','offline-operator-confirmed')
    return data,value

class Cursor:
    def __init__(self,item,changed=None):self.item=item;self.changed=changed
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args):
        assert sql.lstrip().startswith('SELECT'),sql
        if 'md5(to_jsonb' in sql:
            self.result=[('changed' if self.changed=='source' else self.item['source_row_md5'],)]
        else:
            key='capture' if 'supplier_http_captures' in sql else 'observation' if 'offer_commercial_observations' in sql else 'history'
            self.result=[(1 if key==self.changed else 0,)]
    def fetchall(self):return self.result
    def fetchone(self):return self.result[0]

def native_probe(item,changed=None):
    backend=SourceBackend.__new__(SourceBackend)
    backend.repo=SimpleNamespace(connection=SimpleNamespace(cursor=lambda:Cursor(item,changed)))
    return backend.probe

def test_exact_operator_decision_is_not_capture_or_commit(saved):
    data,value=saved;item=data['item'];cp=deepcopy(data['checkpoint'])
    proposed=recovery.continuation_receipts(value,'kami',43,item['input_sha256'],cp)
    receipt=proposed[item['url']]
    assert receipt['state']=='QUARANTINED' and receipt['isolation']['category']=='operator_scope'
    assert 'original_capture_metadata' not in receipt and 'capture_fingerprint' not in receipt
    assert cp==data['checkpoint'] and cp['terminal'][item['url']]['state']=='UNCERTAIN'
    assert native_probe(item)(item['entry'],receipt)=='NOT_FOUND'
    rows=classify([item['entry']],[receipt],native_probe(item),source='kami',parent_job_id=43,manifest_sha256=item['input_sha256'])
    plan=pending_manifest({'supplier_code':'kami','entries':[item['entry']]},rows,parent_job_id=43,parent_manifest_sha256=item['input_sha256'])
    assert plan['automatic_resume_allowed'] and not plan['http_pending']['entries'] and not plan['offline_pending']
    assert len(plan['quarantined'])==1

@pytest.mark.parametrize('changed',['source','capture','observation','history'])
def test_any_sql_ambiguity_stops(saved,changed):
    data,_=saved;item=data['item'];receipt=recovery.scope_quarantine_receipt(item)
    with pytest.raises(PermissionError,match='RECOVERY_SCOPE_'):native_probe(item,changed)(item['entry'],receipt)

@pytest.mark.parametrize('changed',['reason','owner','url','authorization','row'])
def test_exact_case_cannot_be_expanded(saved,changed):
    data,value=saved;item=value['continuation']['quarantine'][0]
    if changed=='reason':item['decision']['reason']='SQL_PERMISSION_ERROR'
    elif changed=='owner':item['decision']['owner_job_id']=84
    elif changed=='url':item['decision']['url']='https://www.stanki.ru/catalog/other/'
    elif changed=='authorization':item['decision']['authorization_sha256']='0'*64
    else:data['checkpoint']['terminal'][item['url']]['receipt']['reason']='SQL_ERROR'
    with pytest.raises(PermissionError):recovery.continuation_receipts(value,'kami',43,item['input_sha256'],data['checkpoint'])

def test_unapproved_scope_receipt_stays_uncertain(saved):
    data,value=saved;item=data['item'];receipt=recovery.scope_quarantine_receipt(item)
    value['continuation']['quarantine']=[]
    rows=classify([item['entry']],[receipt],lambda *a:'NOT_FOUND',source='kami',parent_job_id=43,manifest_sha256=item['input_sha256'])
    assert rows[0]['state']=='UNCERTAIN'
    with pytest.raises(PermissionError):native_probe(item)(item['entry'],receipt)

def test_real_156_committed_and_scope_uncertain_resume_without_http_or_ingest(saved,monkeypatch):
    data,value=saved;item=data['item'];cp=data['checkpoint'];original=data['input']
    folder=Path(os.environ['SOURCE_RUNNER_EVIDENCE_ROOT'])/'kami/job-43';folder.mkdir(parents=True)
    (folder/'checkpoint.json').write_text(json.dumps(cp,ensure_ascii=False),encoding='utf-8')
    (folder/'input.json').write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
    snapshot=deepcopy(data['snapshot']);proof={'kind':'continuation','parent_job_id':84,'owner_job_id':43,'run_id':37,'approval_sha256':'b'*64}
    snapshot['source_batch']['recovery']=proof
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=43,resume_input_sha256=cp['manifest_sha256'])
    monkeypatch.setattr(recovery,'runtime_proof',lambda *a:proof)
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({r['APPROVAL_HOST']:r['APPROVAL_DIGEST_SHA256'] for r in batch.matrix_rows()}))
    job=CrawlJob(92,snapshot['source_input']['canary']['supplier_id'],'kami','kami','https://www.stanki.ru','manual','full','running',None,snapshot)
    backend=Backend();fps={r['receipt']['capture_fingerprint'] for r in cp['terminal'].values() if r['state']=='COMMITTED'}
    def probe(e,r):
        if r.get('isolation',{}).get('category')=='operator_scope':return native_probe(item)(e,r)
        return 'COMMITTED' if r.get('capture_fingerprint') in fps else 'NOT_FOUND'
    backend.probe=probe
    old_classify=runtime.classify
    def reconciled(*args,**kwargs):
        rows=old_classify(*args,**kwargs)
        stop=folder.parent/'job-92/STOP_REQUESTED';stop.parent.mkdir(parents=True,exist_ok=True);stop.write_text('OPERATOR_FIXTURE_STOP_AFTER_RECONCILE')
        return rows
    monkeypatch.setattr(runtime,'classify',reconciled)
    def denied(*a,**kw):raise AssertionError('HTTP MUST NOT RUN')
    token=runtime.LEASED_HOST.set(job.domain)
    try:result=runtime.run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=denied,clock=Clock())
    finally:runtime.LEASED_HOST.reset(token)
    assert result['sql_committed']==156 and result['sql_committed_this_job']==0,result
    assert result['request_metrics']['requests_total']==0 and not backend.writes and not backend.matches
    assert result['status']=='partial' and result['termination_reason'].startswith('OPERATOR_FIXTURE')
    after=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert after['terminal'][item['url']]['state']=='QUARANTINED'
    assert after['terminal'][item['url']]['history'][0]['state']=='UNCERTAIN'
    for url,row in cp['terminal'].items():
        if row['state']=='COMMITTED':assert after['terminal'][url]['receipt']==row['receipt']
    assert len(after['frontier'])==len(cp['frontier'])

@pytest.mark.parametrize('mode',['REFRESH','DISCOVERY'])
def test_later_phase_never_fetches_quarantined_url(saved,monkeypatch,mode):
    data,value=saved;_,job,backend,_,good=setup('kami',mode=mode)
    # Same URL decision applied to a later phase; all transport remains fake.
    value['continuation']['quarantine'][0]['url']=good
    snapshot=deepcopy(job.settings_snapshot);snapshot['source_batch']={'id':value['batch_id'],'deadline':'2026-10-26T11:27:36.560055+00:00'}
    job=replace(job,settings_snapshot=snapshot)
    calls=[]
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            assert url!=good,'Quarantined URL transport is forbidden'
            return HttpResponse(url,200,'text/plain' if url.endswith('robots.txt') else 'text/html',b'User-agent: *\nAllow: /')
    monkeypatch.setattr(runtime,'decode_node',lambda *a:([],[{'url':good,'source_external_id':'any'}]))
    token=runtime.LEASED_HOST.set(job.domain)
    try:result=runtime.run_source(job,None,backend_factory=lambda *a:backend,client_factory=Client,clock=Clock())
    finally:runtime.LEASED_HOST.reset(token)
    assert result['termination_reason']=='OPERATOR_SCOPE_QUARANTINE_NO_HTTP',result
    assert good not in calls and not backend.writes and not backend.matches
