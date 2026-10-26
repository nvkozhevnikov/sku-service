"""Signed frozen-URL refusal policy; real runtime/parser/checkpoint, fake SQL/HTTP."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import gzip,hashlib,json,os
import pytest
from test_source_runtime import isolation,setup,Backend
from test_source_jobs import saved_case
from test_host_fetch import Clock
from universal_supplier.control_plane import source_recovery as recovery,source_runtime as runtime,source_batch as batch
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.http_capture import HttpResponse

FIX=Path(__file__).parent/'fixtures/live11_recovery/kami_scope_stop.json.gz'

def policy_run(tmp_path,monkeypatch,*,failure=None,repeated=False,timeout=False):
    real=json.loads(gzip.decompress(FIX.read_bytes()))
    bad=deepcopy(real['item']['entry'])
    _,job,backend,_,good=setup('kami')
    snapshot=deepcopy(job.settings_snapshot);goodentry=snapshot['source_input']['entries'][0]
    snapshot['supplier_code']='kami'
    goodentry['source_product_id']=999
    snapshot['source_input']['entries'].insert(0,bad);snapshot['crawl_settings']['max_products']=2
    snapshot['source_batch']={'id':real['item']['decision']['batch_id'],'phase':'REFRESH',
        'scope_sha256':'a'*64,'deadline':'2026-10-26T11:27:36.560055+00:00'}
    if repeated:
        # SQL/config are fake in this runtime replay; the independent canary
        # contract tests exercise full selection/target validation.
        from universal_supplier.control_plane import source_canary
        snapshot['source_input']['canary']={'supplier_enabled':True,'supplier_id':1,'max_products':2,'format':source_canary.FORMAT}
        monkeypatch.setattr(source_canary,'validate_config',lambda *a:None)
        monkeypatch.setattr(source_canary,'validate_input',lambda *a:None)
    job=replace(job,settings_snapshot=snapshot)
    auth=tmp_path/'policy-auth.json'
    auth.write_text(json.dumps({'action':'FROZEN_REFRESH_SCOPE_QUARANTINE_AND_R9','batch_id':snapshot['source_batch']['id'],
        'extra_refresh_parents':list(range(92,101)),'refresh_max_attempt':9,'discovery_max_attempt':4}),encoding='utf-8')
    policy={'format':'live11-frozen-scope-quarantine/v1','batch_id':snapshot['source_batch']['id'],
        'authorization_path':str(auth),'authorization_sha256':hashlib.sha256(auth.read_bytes()).hexdigest(),
        'extra_refresh_parents':list(range(92,101)),
        'entries':{'kami':{bad['url']:{'entry':bad,'source_row_md5':'f'*32,
            'input_sha256':digest(snapshot['source_input']),'scope_sha256':'a'*64}}}}
    value={'batch_id':policy['batch_id'],'parents':[{'snapshot':snapshot}],
        'continuation':{'quarantine':[],'scope_policy':policy}}
    monkeypatch.setattr(recovery,'approval',lambda:(value,'b'*64))
    monkeypatch.setattr(batch,'validate_snapshot',lambda *a,**kw:None)
    monkeypatch.setenv(recovery.ENV+'_FILE','offline-confirmed')
    started=datetime(2026,10,23,14,0,tzinfo=timezone.utc)
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args):
            assert sql.lstrip().startswith('SELECT'),sql
            if 'SELECT started_at' in sql:self.rows=[(started,)]
            elif 'j.crawl_run_id,s.code' in sql:self.rows=[(81,'kami',started,snapshot)]
            elif 'SELECT r.metadata' in sql:self.rows=[({'source_runtime':{'owner_job_id':1}},)]
            elif 'md5(to_jsonb' in sql:self.rows=[('changed' if failure=='source' else 'f'*32,)]
            else:
                kind='capture' if 'supplier_http_captures' in sql else 'observation' if 'offer_commercial_observations' in sql else 'history'
                self.rows=[(1 if kind==failure else 0,)]
        def fetchone(self):return self.rows[0]
        def fetchall(self):return self.rows
    connection=SimpleNamespace(cursor=lambda:Cursor())
    native=SourceBackend.__new__(SourceBackend);native.repo=SimpleNamespace(connection=connection)
    backend.repo=SimpleNamespace(connection=connection)
    ordinary_probe=backend.probe
    backend.probe=lambda e,r:native.probe(e,r) if r.get('isolation',{}).get('category')=='operator_scope' else ordinary_probe(e,r)
    body,_,charset,_=saved_case('kami');calls=[];targets=[]
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            if url.endswith('robots.txt'):return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /')
            if url==bad['url']:
                if timeout:raise TimeoutError('The read operation timed out')
                if repeated:self.before_request(url);raise AssertionError('Repeat must never be dispatched')
                target={'host':'https://evil.example/product/','http':'http://www.stanki.ru/catalog/',
                    'credentials':'https://user:password@www.stanki.ru/catalog/','port':'https://www.stanki.ru:8443/catalog/',
                    'fragment':'https://www.stanki.ru/catalog/#fragment','root':'https://www.stanki.ru/'}.get(failure,'https://www.stanki.ru/catalog/')
                validator(target);targets.append(target)
                raise AssertionError('Rejected target must NEVER be dispatched')
            return HttpResponse(url,200,'text/html; charset='+charset,body)
    def run(j=job):
        token=runtime.LEASED_HOST.set(j.domain)
        try:return runtime.run_source(j,None,backend_factory=lambda *a:backend,client_factory=Client,clock=Clock())
        finally:runtime.LEASED_HOST.reset(token)
    return run,job,backend,calls,targets,bad,good,value,connection

def test_native_refusal_is_quarantined_next_card_commits_and_no_target_get(tmp_path,monkeypatch):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch)
    result=run()
    assert result['status']=='succeeded' and result['sql_committed']==1,result
    assert not result['collection_complete'] and not targets
    assert calls==['https://www.stanki.ru/robots.txt',bad['url'],good]
    assert len(backend.writes)==1 and backend.writes[0].requested_url==good and len(backend.matches)==1
    cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'));r=cp['terminal'][bad['url']]
    assert r['state']=='QUARANTINED' and [h['state'] for h in r['history']]==['UNCERTAIN','QUARANTINED']
    assert 'original_capture_metadata' not in r['receipt'] and 'sql_verified' not in r['receipt']
    approved=recovery.approved_scope_item(r['receipt'],bad)
    assert approved['source_row_md5']=='f'*32 and approved['policy_case']

@pytest.mark.parametrize('failure',['source','capture','observation','history','host','http','credentials','port','fragment'])
def test_other_errors_are_still_global_stop(tmp_path,monkeypatch,failure):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch,failure=failure)
    result=run()
    assert result['status']=='partial' and not result['collection_complete'],result
    assert not result['quarantined'] and not backend.writes and not backend.matches and not targets
    cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert cp['terminal'][bad['url']]['state']=='UNCERTAIN'

def test_same_host_root_is_refused_not_followed_and_does_not_stop_rest(tmp_path,monkeypatch):
    run,_,backend,_,targets,_,_,_,_=policy_run(tmp_path,monkeypatch,failure='root')
    result=run()
    assert result['status']=='succeeded' and result['sql_committed']==1 and len(result['quarantined'])==1,result
    assert not targets and len(backend.writes)==1

@pytest.mark.parametrize('field,value',[('action','OTHER'),('batch_id','other'),('refresh_max_attempt',10),('discovery_max_attempt',5)])
def test_signed_policy_does_not_replace_operator_authorization(tmp_path,monkeypatch,field,value):
    run,_,backend,calls,_,_,_,approved,_=policy_run(tmp_path,monkeypatch)
    policy=approved['continuation']['scope_policy'];path=Path(policy['authorization_path'])
    document=json.loads(path.read_text());document[field]=value;path.write_text(json.dumps(document))
    policy['authorization_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    result=run()
    assert result['status']=='partial' and result['termination_reason']=='RECOVERY_SCOPE_POLICY_AUTHORIZATION',result
    assert not calls and not backend.writes and not backend.matches

def test_policy_refusal_evidence_cannot_change_or_expand_url(tmp_path,monkeypatch):
    run,_,_,_,_,bad,_,value,_=policy_run(tmp_path,monkeypatch);result=run()
    cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'));receipt=cp['terminal'][bad['url']]['receipt']
    changed=deepcopy(bad);changed['source_product_id']+=1
    with pytest.raises(PermissionError):recovery.approved_scope_item(receipt,changed)
    ref=receipt['isolation']['policy_evidence'];Path(ref['path']).write_text('{}')
    with pytest.raises(PermissionError,match='EVIDENCE'):recovery.approved_scope_item(receipt,bad)

def test_discovery_reuses_prior_refusal_without_http_or_ingest(tmp_path,monkeypatch):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch)
    result=run();assert result['status']=='succeeded',result
    _,djob,_,_,_=setup('kami',mode='DISCOVERY',backend=backend)
    ds=deepcopy(djob.settings_snapshot)
    ds['source_batch']={**job.settings_snapshot['source_batch'],'phase':'DISCOVERY'}
    value['parents'][0]['snapshot']['source_batch']['next_snapshot']={'source_input':ds['source_input']}
    djob=replace(djob,id=2,settings_snapshot=ds)
    monkeypatch.setattr(runtime,'decode_node',lambda *a:([],[{'url':bad['url']},{'url':good}]))
    before=calls.count(bad['url']);resumed=run(djob)
    assert resumed['status']=='succeeded' and resumed['sql_committed']==1,resumed
    assert calls.count(bad['url'])==before and not targets
    cp=json.loads(Path(resumed['checkpoint']).read_text(encoding='utf-8'));row=cp['terminal'][bad['url']]
    assert row['state']=='QUARANTINED' and row['receipt']['isolation']['reused_refusal']
    assert all(h['state']!='UNCERTAIN' for h in row['history'])  # No invented new transport.

@pytest.mark.parametrize('defect',[None,'unapproved','discovery','parent','ten','spent'])
def test_r9_is_one_exact_bounded_operator_exception(tmp_path,monkeypatch,defect):
    _,_,_,_,_,_,_,value,_=policy_run(tmp_path,monkeypatch)
    legacy=json.loads(gzip.decompress((FIX.parent/'post_stop_41_50.json.gz').read_bytes()))['manifest']
    legacy['continuation']={'format':'live11-continuation/v1','batch_id':legacy['batch_id'],
        'scope_policy':value['continuation']['scope_policy'],'phases':{s:{'max_attempt':8} for s in legacy['scope_hashes']},
        'manual_hashes':legacy['post_stop']['manual_hashes'],'mappings1282':legacy['post_stop']['mappings1282']}
    scope=legacy['scope_hashes'][0];rule=legacy['continuation']['phases'][scope]
    rule.update(max_attempt=9,phase='REFRESH',r9_parent_id=92)
    if defect=='unapproved':legacy['continuation'].pop('scope_policy')
    elif defect=='discovery':rule['phase']='DISCOVERY'
    elif defect=='parent':rule['r9_parent_id']=999
    elif defect=='ten':rule['max_attempt']=10
    monkeypatch.setattr(recovery,'approval',lambda:(legacy,'b'*64))
    snapshot={'source_batch':{'scope_sha256':scope,'attempt':8}}
    proof={'kind':'continuation','approval_sha256':'b'*64,'parent_job_id':92}
    if defect:
        if defect=='spent':snapshot['source_batch']['attempt']=9
        count=snapshot['source_batch']['attempt']
        with pytest.raises(PermissionError):recovery.continuation_envelope(snapshot,count,count,proof)
    else:
        envelope=recovery.continuation_envelope(snapshot,8,8,proof)
        assert envelope['attempt']==9
