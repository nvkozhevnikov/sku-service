"""One approved snapshot, internal gate, host profiles: no HTTP/SQL services."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import threading
import pytest
from fastapi.testclient import TestClient
from offline_host_profiles import approved_policy,approval_for
from test_source_canary import FakeDB,offline_env,store_for
from test_source_jobs import saved_case
from test_source_runtime import Backend,setup
from test_host_fetch import Clock
from test_partner_pilot import CFG
from universal_supplier.control_plane.source_jobs import RUNTIME_SOURCES,SOURCES,parse_existing
from universal_supplier.control_plane.source_discovery import HOSTS,root_urls
from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST
from universal_supplier.control_plane.source_canary import build_preview
from universal_supplier.control_plane.host_fetch import compile_policy,BoundedHostFetcher
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.control_plane.web import create_app
from universal_supplier.http_capture import HttpResponse,sanitise_html


def exercise(monkeypatch,code,total=17,*,status=200,fail_probe=False,lost_response=False,mode='REFRESH',seconds=7200,backend=None,snapshot=None,job_id=71,concurrency=1,cap=None):
    """Use saved real parser model, unique synthetic URLs, and receipt readback."""
    if code=='partner_st':
        from test_partner_pilot import html
        body,url,charset=html(1).encode(),'https://partner-st.ru/item-1/','utf-8'
    elif code=='optimum':
        from test_optimum_source import HTML,URL
        body,url,charset=HTML.encode(),URL,'utf-8'
    else:body,url,charset,_=saved_case(code)
    model=parse_existing(code,sanitise_html(body,'text/html; charset='+charset).decode(),url)
    base='https://'+HOSTS[code]
    rows=[(i+1,str(30000+i),url.replace('/bmso-460c/',f'/bmso-460c-offline-{i}/') if code=='beka_mak_tr' else url.rstrip('/')+f'-offline-{i}/',total) for i in range(total)]
    rate=approved_policy(code,base,{'min_request_interval_seconds':1.,'max_concurrency':concurrency,'jitter_min_seconds':0.,'jitter_max_seconds':0.,'max_attempts':1,'max_run_duration_seconds':7200.})
    supplier=(code,code,base,True,{'host_policy_approval':approval_for(base),'max_run_duration_seconds':seconds},rate.snapshot())
    cap=total if cap is None else cap
    snapshot=snapshot or build_preview(1,supplier,rows[:cap],CFG,cap,mode)['snapshot']
    entries=snapshot['source_input']['entries'] if mode=='REFRESH' else [{'source_product_id':sid,'source_external_id':ext,'url':u} for sid,ext,u,_ in rows]
    byurl={e['url']:e for e in entries};backend=backend or Backend();calls=[];clock=Clock()
    original_probe=backend.probe
    if fail_probe:backend.probe=lambda e,r:'UNKNOWN'
    def parsed(source,text,target):
        e=byurl[target]
        if source in ('kami','partner_st','optimum'):return replace(model,external_id=e['source_external_id'],requested_url=target,final_url=target,canonical_url=target)
        return replace(model,source_url=target,site_internal_id=e['source_external_id'])
    monkeypatch.setattr('universal_supplier.control_plane.source_runtime.parse_existing',parsed)
    monkeypatch.setattr('universal_supplier.control_plane.source_runtime.decode_node',lambda *a:([],list(byurl.values())))
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            validator(target);self.before_request(target);calls.append(target)
            if target.endswith('robots.txt'):return HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /')
            if target in root_urls(code):return HttpResponse(target,200,'application/xml',b'<urlset/>')
            # This assertion is at the transport boundary, before card six.
            if len([u for u in calls if u in byurl])>5:
                assert len(backend.saved)>=5
                assert all(original_probe({}, {'capture_fingerprint':fp})=='COMMITTED' for fp in list(backend.saved)[:5])
            assert target in byurl
            if lost_response:raise ConnectionError('OFFLINE_LOST_RESPONSE_AFTER_TRANSPORT_ENTRY')
            return HttpResponse(target,status,'text/html',b'<h1>offline card</h1>')
    job=CrawlJob(job_id,1,code,code,base,'manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
    finally:LEASED_HOST.reset(token)
    return result,backend,calls,snapshot


@pytest.mark.parametrize('code',RUNTIME_SOURCES)
def test_all_eleven_one_job_continues_beyond_first_five(monkeypatch,code):
    result,backend,calls,snapshot=exercise(monkeypatch,code)
    assert result['status']=='succeeded',result
    assert result['sql_committed']==17 and result['remaining']==0
    assert len(backend.writes)==17 and len(backend.matches)==1
    assert result['safety_gate_passed'] and result['approved_total_cap']==17
    assert len(calls)==18 and len(set(calls))==18
    assert len(snapshot['source_input']['entries'])==17
    state=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert len(state['terminal'])==17 and all(v['receipt']['sql_verified'] for v in state['terminal'].values())


def test_five_hundred_cards_in_one_immutable_job(monkeypatch):
    result,backend,calls,snapshot=exercise(monkeypatch,'beka_mak',500,concurrency=4)
    assert result['status']=='succeeded' and result['sql_committed']==500,result
    assert len(calls)==501 and len(backend.writes)==500 and len(snapshot['source_input']['entries'])==500


@pytest.mark.parametrize('status',[403,429])
def test_host_block_is_terminal_inside_large_job(monkeypatch,status):
    result,backend,calls,_=exercise(monkeypatch,'beka_mak',status=status)
    assert result['termination_reason']==f'SOURCE_BLOCKED_HTTP_{status}'
    assert len(calls)==2 and not backend.writes and not result['safety_gate_passed']


def test_unproven_sql_gate_never_dispatches_sixth(monkeypatch):
    result,backend,calls,_=exercise(monkeypatch,'beka_mak',fail_probe=True)
    assert result['termination_reason']=='SQL_COMMIT_NOT_PROVEN'
    assert len(calls)==2 and len(backend.writes)==1 and not result['resume_available']


@pytest.mark.parametrize('code',['beka_mak','partner_st','optimum'])
def test_discovery_and_same_job_card_gate(monkeypatch,code):
    result,backend,calls,_=exercise(monkeypatch,code,mode='DISCOVERY')
    assert result['status']=='succeeded' and result['sql_committed']==17,result
    assert len(backend.writes)==17 and result['safety_gate_passed']
    assert set(root_urls(code)).intersection(calls)


def test_timed_stop_and_resume_keep_total_scope_and_skip_committed(monkeypatch):
    first,backend,calls,snapshot=exercise(monkeypatch,'beka_mak',seconds=9)
    assert first['status']=='partial' and first['sql_committed']>=5 and first['resume_available'],first
    committed={c.requested_url for c in backend.writes}
    resumed=deepcopy(snapshot)
    resumed['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=71,resume_input_sha256=first['input_sha256'])
    second,backend,more,_=exercise(monkeypatch,'beka_mak',backend=backend,snapshot=resumed,job_id=72)
    assert not committed.intersection(more) and second['sql_committed']>first['sql_committed'],second
    assert second['approved_total_cap']==17 and len(resumed['source_input']['entries'])==17


@pytest.mark.parametrize('mode',[None,'REFRESH','DISCOVERY'])
def test_generic_queue_cannot_bypass_explicit_cap_and_preview(mode):
    db=FakeDB();queue=db.queue()
    with pytest.raises(PermissionError,match='EXPLICIT_COLLECTION_SCOPE'):
        queue.enqueue(11,trigger_type='manual',crawl_type='full',source_mode=mode)
    assert not db.snapshots


def test_ui_accepts_total_500_but_digest_still_binds_selected_109():
    db=FakeDB();queue=db.queue();client=TestClient(create_app(store=store_for(db),queue=queue,auth_required=False))
    page=client.get('/suppliers/11/source-canary?max_products=500')
    assert page.status_code==200 and '500' in page.text and '109' in page.text
    preview=queue.preview_source_canary(11,500)
    reply=client.post('/suppliers/11/start',data={'launch_mode':'source_canary','collection_mode':'REFRESH','max_products':'500','source_preview_sha256':preview['confirmation_sha256']},headers={'origin':'http://testserver'})
    assert reply.status_code==200
    assert db.snapshots[0]['crawl_settings']['max_products']==500 and len(db.snapshots[0]['source_input']['entries'])==109


@pytest.mark.parametrize('interval,concurrency,jitter',[(.3,3,.07),(1.7,2,.13),(8.,1,.2)])
def test_distinct_explicit_profiles_shared_pacing_jitter_and_concurrency(monkeypatch,interval,concurrency,jitter):
    rate=approved_policy('vekprom','https://vekprom.ru',{'min_request_interval_seconds':interval,'max_concurrency':concurrency,'jitter_min_seconds':jitter,'jitter_max_seconds':jitter})
    clock=Clock();starts=[];active=0;maximum=0;lock=threading.Lock()
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            nonlocal active,maximum
            self.before_request(url)
            with lock:active+=1;maximum=max(maximum,active);starts.append(clock.monotonic())
            with lock:active-=1
            return HttpResponse(url,200,'text/html',b'ok')
    fetcher=BoundedHostFetcher('vekprom.ru',rate,Client,validator=lambda u:None,robots_allowed=lambda u:True,robots_delay_seconds=0,lease_acquired=True,source='vekprom',approval=approval_for('https://vekprom.ru'),clock=clock)
    for batch in range(2):assert len(list(fetcher.fetch([f'https://vekprom.ru/{batch}/{i}' for i in range(8)],limit=20,seconds=300)))==8
    assert 1<=maximum<=concurrency
    assert all(b-a>=interval+jitter-1e-9 for a,b in zip(sorted(starts),sorted(starts)[1:]))


@pytest.mark.parametrize('code',SOURCES)
def test_no_unconfirmed_host_has_an_implicit_speed(code):
    with pytest.raises(PermissionError,match='EXPLICIT_HOST_PROFILE_REQUIRED'):compile_policy(code,'https://'+HOSTS[code])


def test_missing_response_after_transport_entry_stays_uncertain_even_attempt_count_zero(monkeypatch):
    result,backend,calls,_=exercise(monkeypatch,'beka_mak',lost_response=True)
    assert not result['resume_available'] and len(result['resume_uncertain'])==1
    assert len(calls)==2 and not backend.writes


def test_not_requested_needs_positive_pre_transport_proof(tmp_path):
    from universal_supplier.control_plane.source_resume import SourceCheckpoint
    url='https://vekprom.ru/product/'
    cp=SourceCheckpoint(tmp_path,'vekprom',1,'a'*64,validator=lambda u:None,lease_acquired=True)
    cp.add_urls([url]);cp.claim_batch(1)
    with pytest.raises(ValueError,match='NOT_REQUESTED_PROOF_REQUIRED'):
        cp.return_not_requested({'url':url,'state':'NOT_REQUESTED'})
    assert url in cp.state['inflight'] and not cp.state['frontier']
    cp.return_not_requested({'url':url,'state':'NOT_REQUESTED','transport_started':False})
    assert not cp.state['inflight'] and cp.state['frontier']==[url]


def test_approved_retry_budget_does_not_add_a_sixth_identity():
    from universal_supplier.control_plane.source_canary import DispatchBudget
    db=FakeDB();supplier=(*db.supplier[:5],approved_policy(db.code,db.supplier[2],{'max_attempts':2}).snapshot())
    value=build_preview(11,supplier,db.rows[:5],CFG,5)['snapshot']['source_input']
    guard=DispatchBudget(value,'https://beka-mak.su/robots.txt')
    for entry in value['entries']:
        guard(entry['url']);guard(entry['url'])
    from universal_supplier.control_plane.rate_control import RequestAborted
    with pytest.raises(RequestAborted):guard(db.rows[5][2])
    with pytest.raises(RequestAborted):guard(db.rows[0][2])
    assert len(guard.products)==10 and len(set(guard.products))==5


@pytest.mark.parametrize('code',RUNTIME_SOURCES)
def test_runtime_has_no_implicit_100_card_budget(code):
    run,job,backend,calls,_=setup(code)
    job.settings_snapshot['crawl_settings'].pop('max_products')
    with pytest.raises(ValueError,match='SOURCE_URL_BUDGET'):run()
    assert not calls and not backend.writes


def test_discovery_does_not_download_candidates_beyond_approved_cap(monkeypatch):
    result,backend,calls,_=exercise(monkeypatch,'beka_mak',total=32,mode='DISCOVERY',cap=10)
    assert result['status']=='succeeded' and result['sql_committed']==10,result
    assert len(backend.writes)==10 and result['selected_urls']==10 and result['discovered_urls_total']==32
    assert len(calls)==12 and not result['deactivation_allowed']


def test_discovery_preview_and_queue_freeze_mode_cap_seeds_before_enqueue():
    db=FakeDB();queue=db.queue()
    preview=queue.preview_source_canary(11,500,'DISCOVERY')
    queue.enqueue(11,trigger_type='manual',crawl_type='full',source_mode='DISCOVERY',source_limit=500,source_preview_sha256=preview['confirmation_sha256'])
    snapshot=db.snapshots[0]
    assert snapshot['source_input']['mode']=='DISCOVERY' and snapshot['source_input']['seeds']==root_urls('beka_mak')
    assert snapshot['source_input']['entries']==[] and snapshot['crawl_settings']['max_products']==500
