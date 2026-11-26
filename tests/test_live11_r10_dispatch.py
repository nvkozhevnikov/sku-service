"""Real denial/checkpoint paths; fake HTTP/SQL and signed offline authorization."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import gzip,hashlib,json
import pytest
from test_live11_scope_policy import policy_run
from test_source_runtime import isolation
from universal_supplier.control_plane import source_recovery as recovery,source_batch as batch
from universal_supplier.control_plane.source_canary import DispatchBudget,FORMAT
from universal_supplier.control_plane.host_fetch import RepeatedDispatchRefusal
from universal_supplier.control_plane.rate_control import RequestAborted

def extension(value,tmp_path):
    value.update(started_at='2026-10-22T11:27:36.560055+00:00',deadline='2026-10-26T11:27:36.560055+00:00',
        matrix_sha256=batch.MATRIX_SHA,scope_hashes=value.get('scope_hashes',['a'*64]),input_hashes=['b'*64])
    auth={'action':'REPEATED_DISPATCH_QUARANTINE_R10_AND_WINDOW','batch_id':value['batch_id'],
        'original_started_at':value['started_at'],'original_deadline':value['deadline'],
        'execution_until':'2026-10-27T11:27:36+00:00','extra_refresh_parents':list(range(101,110)),
        'refresh_max_attempt':10,'discovery_max_attempt':4,'matrix_sha256':value['matrix_sha256'],
        'scope_hashes':value['scope_hashes'],'input_hashes':value['input_hashes'],
        'previous_approval_sha256':'66a80778748d79155f53cc1ce727029b8c3bb50ad2d5fde710a300e44da05b87'}
    path=tmp_path/'r10-auth.json';path.write_text(json.dumps(auth),encoding='utf-8')
    value['continuation']['operator_extension']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return auth,path

@pytest.mark.parametrize('failure',[None,'source','capture','observation','history'])
def test_repeat_denied_without_retry_or_fake_response_and_other_urls_progress(tmp_path,monkeypatch,failure):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch,repeated=True,failure=failure)
    extension(value,tmp_path)
    result=run();cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert calls.count(bad['url'])==1 and not targets
    row=cp['terminal'][bad['url']]
    if failure:
        assert result['status']=='partial' and row['state']=='UNCERTAIN' and not backend.writes
    else:
        assert result['status']=='succeeded' and result['sql_committed']==1,result
        assert row['state']=='QUARANTINED' and row['history'][0]['state']=='UNCERTAIN'
        assert row['receipt']['isolation']['http_response_available'] is False
        assert len(backend.writes)==1 and backend.writes[0].requested_url==good
        assert 'original_capture_metadata' not in row['receipt']

def test_unsigned_repeat_and_other_denials_still_stop():
    value={'entries':[{'url':'https://example.com/item/'}],'canary':{'max_products':1,'format':FORMAT},'rate_policy':{'max_attempts':1}}
    for signed in (False,True):
        budget=DispatchBudget(value,'https://example.com/robots.txt',isolate_repeats=signed)
        budget(value['entries'][0]['url'])
        with pytest.raises(RepeatedDispatchRefusal if signed else RequestAborted):budget(value['entries'][0]['url'])
        assert len(budget.products)==1
        with pytest.raises(RequestAborted):budget('https://example.com/outside/')
        budget(budget.robots_url)
        with pytest.raises(RequestAborted):budget(budget.robots_url)

@pytest.mark.parametrize('kind',['repeat','scope'])
def test_local_refusal_never_uses_network_retries_or_opens_host_breaker(kind):
    from test_host_fetch import fetcher
    from offline_host_profiles import approved_policy
    from universal_supplier.control_plane.host_fetch import FrozenScopeRefusal
    from universal_supplier.http_capture import HttpResponse
    rate=approved_policy('vekprom','https://vekprom.ru/',{'max_attempts':3,'jitter_max_seconds':0})
    bad='https://vekprom.ru/item/';good='https://vekprom.ru/other/'
    f,_=fetcher(rate=rate);calls=[]
    value={'entries':[{'url':bad},{'url':good}],'canary':{'max_products':2,'format':FORMAT},'rate_policy':rate.snapshot()}
    budget=DispatchBudget(value,'https://vekprom.ru/robots.txt',isolate_repeats=True)
    if kind=='repeat':budget.products=[bad]*3  # Already admitted, not a fabricated response.
    f.request_start_guard=budget
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            if url==bad:raise FrozenScopeRefusal('https://vekprom.ru/refused/')
            return HttpResponse(url,200,'text/html',b'<h1>Machine</h1>')
    f.client_factory=Client
    outcomes=list(f.fetch([bad,good],limit=2,seconds=300))
    assert f.stop_reason is None and f.metrics.retry_count==f.metrics.circuit_breaker_trips==0
    assert calls.count(bad)==(0 if kind=='repeat' else 1) and calls.count(good)==1
    assert len([x for x in outcomes if x.response and x.response.status_code==200])==1
    assert any(x.dispatch_refusal if kind=='repeat' else x.scope_refusal for x in outcomes)

@pytest.mark.parametrize('field,new',[('execution_until','2026-10-28T11:27:36+00:00'),('original_deadline','other'),
    ('extra_refresh_parents',[102]),('refresh_max_attempt',11),('discovery_max_attempt',5),('scope_hashes',['changed']),
    ('matrix_sha256','wrong'),('batch_id','other')])
def test_extension_rejects_unapproved_window_scope_and_attempts(tmp_path,field,new):
    value={'batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','continuation':{}}
    auth,path=extension(value,tmp_path);auth[field]=new;path.write_text(json.dumps(auth),encoding='utf-8')
    value['continuation']['operator_extension']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PermissionError):recovery.extension_authorization(value)

def test_original_deadline_and_explicit_effective_deadline_are_separate(tmp_path):
    value={'batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','continuation':{}}
    extension(value,tmp_path);original=value['deadline']
    assert recovery.execution_deadline(value)==datetime(2026,10,27,11,27,36,tzinfo=timezone.utc)
    assert value['deadline']==original
    value['continuation'].pop('operator_extension')
    assert recovery.execution_deadline(value)==datetime.fromisoformat(original)

@pytest.mark.parametrize('defect',[None,'eleven','discovery','parent','spent','unsigned'])
def test_r10_is_single_explicit_refesh_exception(tmp_path,monkeypatch,defect):
    fixture=Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz'
    value=json.loads(gzip.decompress(fixture.read_bytes()))['manifest']
    value['continuation']={'format':'live11-continuation/v1','batch_id':value['batch_id'],
        'phases':{s:{'max_attempt':8} for s in value['scope_hashes']},
        'manual_hashes':value['post_stop']['manual_hashes'],'mappings1282':value['post_stop']['mappings1282']}
    extension(value,tmp_path);scope=value['scope_hashes'][0]
    rule=value['continuation']['phases'][scope];rule.update(max_attempt=10,phase='REFRESH',r10_parent_id=101)
    if defect=='eleven':rule['max_attempt']=11
    elif defect=='discovery':rule['phase']='DISCOVERY'
    elif defect=='parent':rule['r10_parent_id']=999
    elif defect=='unsigned':value['continuation'].pop('operator_extension')
    monkeypatch.setattr(recovery,'approval',lambda:(value,'b'*64))
    snapshot={'source_batch':{'scope_sha256':scope,'attempt':10 if defect=='spent' else 9}}
    proof={'kind':'continuation','approval_sha256':'b'*64,'parent_job_id':101}
    if defect:
        with pytest.raises(PermissionError):recovery.continuation_envelope(snapshot,snapshot['source_batch']['attempt'],snapshot['source_batch']['attempt'],proof)
    else:assert recovery.continuation_envelope(snapshot,9,9,proof)['attempt']==10

def test_effective_window_requires_same_original_envelope(tmp_path,monkeypatch):
    fixture=Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz'
    value=json.loads(gzip.decompress(fixture.read_bytes()))['manifest']
    value['continuation']={'format':'live11-continuation/v1','batch_id':value['batch_id'],
        'phases':{s:{'max_attempt':8} for s in value['scope_hashes']},
        'manual_hashes':value['post_stop']['manual_hashes'],'mappings1282':value['post_stop']['mappings1282']}
    value['continuation']['historical_envelopes']=[]
    extension(value,tmp_path);envelope=deepcopy(value['envelopes'][0])
    monkeypatch.setattr(recovery,'approval',lambda:(value,'b'*64))
    monkeypatch.setattr(batch,'current_head',lambda:'new-head')
    monkeypatch.setenv(recovery.ENV+'_FILE','confirmed-offline')
    original=deepcopy(envelope)
    batch.deadline_check(envelope,datetime(2026,10,26,12,tzinfo=timezone.utc))
    assert envelope==original
    with pytest.raises(PermissionError,match='BATCH_WINDOW_EXPIRED'):
        batch.deadline_check(envelope,datetime(2026,10,27,11,27,36,tzinfo=timezone.utc))
    for key in ('id','deadline','scope_sha256','matrix_sha256'):
        changed=deepcopy(envelope);changed[key]='other'
        with pytest.raises((PermissionError,ValueError)):recovery.approved_execution_deadline(changed)

@pytest.mark.parametrize('defect',[None,'artifact','fingerprint','mapping','status','http'])
def test_matching_reuse_requires_signed_durable_native_results(tmp_path,defect):
    cp={'terminal':{'https://example.com/product/':{'state':'COMMITTED','receipt':{'external_id':'id1','capture_fingerprint':'a'*64}}}}
    before={'selected':{'kami':{'ids':['id1'],'fingerprints':['a'*64]}},'checkpoint_sha256':{'original':'b'*64},'state':{'mappings':[['unchanged']]}}
    durable={'committed':True,'http':0,'ingest':0,'original1282_exact':True,'protected_tables_exact':True,
        'checkpoint_sha256':before['checkpoint_sha256'],'results':{'kami':{'status':'succeeded','apply_allowed':True,
        'f01_f03_enforced':True,'existing_ids_preserved':True,'total':1,'policy_version':'native'}}}
    value={'continuation':{'mappings1282':before['state']['mappings'],'matching_only':{}}}
    if defect=='fingerprint':cp['terminal']['https://example.com/product/']['receipt']['capture_fingerprint']='c'*64
    elif defect=='mapping':value['continuation']['mappings1282']=[['changed']]
    elif defect=='status':durable['results']['kami']['status']='partial'
    elif defect=='http':durable['http']=1
    for key,doc in [('before',before),('durable',durable)]:
        path=tmp_path/(key+'.json');path.write_text(json.dumps(doc),encoding='utf-8')
        value['continuation']['matching_only'][key]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if defect=='artifact':path.write_text('{}')
    if defect:
        with pytest.raises(PermissionError):recovery.matching_only_receipts(value,'kami',cp)
    else:
        receipts=recovery.matching_only_receipts(value,'kami',cp)
        assert receipts['id1']['status']=='succeeded' and 'matching_only_proof' in receipts['id1']
        assert recovery.matching_only_receipts(value,'other',cp)=={}
