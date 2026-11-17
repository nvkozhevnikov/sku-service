from copy import deepcopy
from offline_host_profiles import approved_policy,approval_for
from datetime import datetime,timezone
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
import json,hashlib,threading,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_source_runtime import setup,Backend
from test_host_fetch import Clock
from test_readiness_panel import PanelStore
from universal_supplier.control_plane.source_controls import prepare_snapshot
from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST,LEASE_ALIVE,job_dir
from universal_supplier.control_plane.source_discovery import HOSTS,root_urls,decode_node
from universal_supplier.control_plane.host_fetch import compile_policy,policy_digest,BoundedHostFetcher
from universal_supplier.control_plane.rate_control import DomainRatePolicy
from universal_supplier.control_plane.web import create_app
from universal_supplier.http_capture import HttpResponse
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.control_plane.presentation import phase_summary

@pytest.fixture(autouse=True)
def offline_env(monkeypatch,tmp_path):
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES');monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES');monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'run'))

def test_panel_modes_only_when_enabled_and_no_unsupported_resume(monkeypatch):
    monkeypatch.delenv('STERBRUST_PARTNER_PILOT',raising=False)
    store=PanelStore('kami');client=TestClient(create_app(store=store,auth_required=False))
    text=client.get('/suppliers/1').text
    assert 'value="REFRESH"' in text and 'value="DISCOVERY"' in text and 'value="RESUME"' not in text
    monkeypatch.delenv('STERBRUST_SOURCE_RUNTIME_ENABLED')
    text=client.get('/suppliers/1').text
    assert 'Исполнитель сбора выключен' in text and 'name="collection_mode"' not in text

def test_panel_route_passes_only_explicit_modes_to_same_queue():
    class Queue:
        def enqueue(self,*args,**kw):self.kw=kw;return SimpleNamespace(job_id=9,status='queued',deduplicated_job_id=None)
    queue=Queue();client=TestClient(create_app(store=PanelStore('kami'),queue=queue,auth_required=False))
    response=client.post('/suppliers/1/start',data={'launch_mode':'source','collection_mode':'RESUME','resume_job_id':'1'},headers={'origin':'http://testserver'},follow_redirects=False)
    assert response.status_code==303 and queue.kw['source_mode']=='RESUME' and queue.kw['trigger_type']=='manual'
    response=client.post('/suppliers/1/start',data={'launch_mode':'source','collection_mode':'invented'},headers={'origin':'http://testserver'})
    assert response.status_code==400

def test_queue_freezes_source_wide_refresh_and_separate_discovery():
    snap={'supplier_code':'intervesp','adapter_name':'intervesp','domain':'intervesp.ru','crawl_settings':{'host_policy_approval':approval_for('https://intervesp.ru')},'rate_settings':approved_policy('intervesp','https://intervesp.ru').snapshot()}
    old=deepcopy(snap)
    refresh=prepare_snapshot(snap,'REFRESH',known_rows=[('123','https://intervesp.ru/catalog/lathe/model/')])
    discovery=prepare_snapshot(snap,'DISCOVERY')
    assert snap==old and refresh['source_input']['entries'][0]['source_external_id']=='123'
    assert not discovery['source_input']['entries'] and discovery['source_input']['seeds']==root_urls('intervesp')
    assert discovery['rate_settings']['min_request_interval_seconds']==20

def test_unapproved_speed_change_cannot_enter_queue(monkeypatch):
    snap={'supplier_code':'vekprom','adapter_name':'vekprom','domain':'vekprom.ru','crawl_settings':{},'rate_settings':{'min_request_interval_seconds':1,'max_concurrency':2,'request_timeout_seconds':20,'jitter_min_seconds':0,'jitter_max_seconds':0.1,'max_attempts':1,'max_run_duration_seconds':300}}
    with pytest.raises(PermissionError,match='APPROVED'):prepare_snapshot(snap,'DISCOVERY')

@pytest.mark.parametrize('field,value',[('source','kami'),('job_id',999),('manifest_sha256','a'*64)])
def test_panel_resume_rejects_foreign_discovery_checkpoint(field,value):
    run,job,backend,calls,url=setup('vekprom','DISCOVERY')
    job.settings_snapshot['crawl_settings']['max_discovery_pages']=1
    result=run()
    from universal_supplier.control_plane.source_runtime import evidence_root,read_input
    from universal_supplier.control_plane.source_controls import verify_resume
    from universal_supplier.control_plane.source_resume import digest
    path=evidence_root()/'discovery'/'vekprom'/'job-1'/'checkpoint.json'
    state=json.loads(path.read_text(encoding='utf-8'));state.pop('checksum')
    state[field]=value;state['checksum']=digest(state)
    path.write_text(json.dumps(state),encoding='utf-8')
    original=read_input('vekprom',1,result['input_sha256'])
    with pytest.raises(ValueError,match='UNCERTAIN_DISCOVERY'):
        verify_resume('vekprom',1,result['input_sha256'],original,None)

@pytest.mark.parametrize('code',[code for code in HOSTS if code not in ('partner_st','optimum')])
def test_host_bound_approval_accepts_exact_policy_only(monkeypatch,code):
    host=HOSTS[code].removeprefix('www.');rate=DomainRatePolicy(max_concurrency=2,min_request_interval_seconds=1,max_attempts=1,request_timeout_seconds=20,max_run_duration_seconds=300)
    approval={'host':host,'reference':'OFFLINE_SYNTHETIC_APPROVAL'}
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({host:policy_digest(code,host,rate,approval['reference'])}))
    assert compile_policy(code,'https://'+HOSTS[code],rate.snapshot(),approval=approval)==rate
    with pytest.raises(PermissionError):compile_policy(code,'https://'+HOSTS[code],{**rate.snapshot(),'max_concurrency':3},approval=approval)

@pytest.mark.parametrize('code',['kami','vekprom'])
def test_large_frontier_cannot_exceed_explicit_total_scope(tmp_path,code):
    run,job,backend,calls,url=setup(code)
    entries=[deepcopy(job.settings_snapshot['source_input']['entries'][0])]
    for i in range(1500):entries.append({'url':url.rstrip('/')+'-'+str(i)+'/'})
    job.settings_snapshot['source_input']['entries']=entries
    job.settings_snapshot['crawl_settings']['max_products']=1
    with pytest.raises(ValueError,match='SNAPSHOT_EXCEEDS_APPROVED_TOTAL_CAP'):run()
    assert not calls and not backend.writes

def test_resume_uncertain_inflight_is_never_refetched():
    run,job,backend,calls,url=setup('kami');first=run()
    p=Path(first['checkpoint']);state=json.loads(p.read_text(encoding='utf-8'));state.pop('checksum')
    state['inflight'][url]={'state':'UNCERTAIN'};state['terminal'].pop(url)
    from universal_supplier.control_plane.source_resume import digest
    state['checksum']=digest(state);p.write_text(json.dumps(state),encoding='utf-8')
    resumed=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    before=list(calls);result=run(resumed)
    assert 'UNCERTAIN' in result['termination_reason'] and calls==before and not result['resume_available']

def test_matching_resumes_after_sql_commit_without_repeat_write():
    backend=Backend();backend.fail='after';run,job,backend,calls,url=setup('kami',backend=backend)
    first=run();backend.fail=None;before=list(calls)
    resumed=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    result=run(resumed)
    assert result['status']=='succeeded' and len(backend.matches)==1 and len(backend.writes)==1 and calls==before

def test_lease_loss_stops_before_sql_and_retains_capture():
    run,job,backend,calls,url=setup('kami')
    # Simulate loss after robots and product response, before persistence.
    token=LEASE_ALIVE.set(lambda:len(calls)<2)
    try:result=run()
    finally:LEASE_ALIVE.reset(token)
    assert result['termination_reason']=='HOST_LEASE_LOST' and not backend.writes and not backend.matches
    assert (job_dir('kami',1)/'FIRST_STOP.json').is_file()

@pytest.mark.parametrize('kind',['none','missing_observation','wrong_sha','duplicate','good'])
@pytest.mark.parametrize('external_id',['987',None])
def test_production_backend_uses_exact_capture_and_observation_readback(kind,external_id):
    from universal_supplier.commercial_persistence import HttpCapture
    from universal_supplier.control_plane.source_runtime import receipt_for
    cap=HttpCapture('https://vekprom.ru/catalog/a/b/','https://vekprom.ru/catalog/a/b/',200,'text/html',datetime.now(timezone.utc),'a'*64,'b'*64,'capture://offline/fixture')
    row=(cap.evidence_sha256,cap.observed_at,cap.final_url,True)
    rows=[] if kind=='none' else [row,row] if kind=='duplicate' else [('c'*64,*row[1:])] if kind=='wrong_sha' else [(*row[:3],False)] if kind=='missing_observation' else [row]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,args):self.q=q;self.args=args
        def fetchall(self):return rows
    cursor=Cursor();backend=SourceBackend.__new__(SourceBackend);backend.code='vekprom';backend.repo=SimpleNamespace(connection=SimpleNamespace(cursor=lambda:cursor))
    receipt=receipt_for(cap.requested_url,'HTTP_CAPTURED_NOT_COMMITTED',cap,'vekprom',1,'f'*64,external_id)
    result=backend.probe({'url':cap.requested_url},receipt)
    assert result==('COMMITTED' if kind=='good' else 'NOT_FOUND' if kind=='none' else 'UNKNOWN')
    assert cursor.q.strip().startswith('SELECT') and cursor.args==('vekprom',cap.fingerprint,cap.requested_url,external_id,external_id)
    assert '%s::text IS NULL' in cursor.q  # Native typed nullable-ID contract; no SQL type ambiguity.

def test_summary_never_counts_failed_http_as_parsed_or_sql():
    summary=phase_summary({'metadata':{'source_runtime':{'processed':3,'parsed_this_job':1,'sql_committed_this_job':0,'request_metrics':{'http_2xx':2}}}})
    assert summary['parsed']==1 and summary['sql_count']==0 and summary['matching'] is None

def test_source_specific_kami_pagination_and_no_filter_endpoints():
    url='https://www.stanki.ru/catalog/'
    html=b'<a href="/catalog/a/">a</a><a data-pagination-button href="/catalog/a/?PAGEN_2=2">next</a><a href="/catalog/a/?sort=price">sort</a>'
    children,products=decode_node('kami',url,html)
    assert children==['https://www.stanki.ru/catalog/a/','https://www.stanki.ru/catalog/a/?PAGEN_2=2'] and not products

def test_shared_gate_survives_multiple_batches_and_redirect_pacing():
    clock=Clock();calls=[]
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            self.before_request(url);calls.append((url,clock.monotonic()))
            if url.endswith('/one'):
                self.before_request('https://vekprom.ru/redirect');calls.append(('redirect',clock.monotonic()))
                return HttpResponse('https://vekprom.ru/redirect',200,'text/html',b'<h1>ok</h1>',redirects=('https://vekprom.ru/redirect',))
            return HttpResponse(url,200,'text/html',b'<h1>ok</h1>')
    rate=approved_policy('vekprom','https://vekprom.ru',{'jitter_max_seconds':0})
    f=BoundedHostFetcher('vekprom.ru',rate,Client,validator=lambda u:None,robots_allowed=lambda u:True,robots_delay_seconds=0,lease_acquired=True,source='vekprom',approval=approval_for('https://vekprom.ru'),clock=clock)
    list(f.fetch(['https://vekprom.ru/one'],limit=10,seconds=300));list(f.fetch(['https://vekprom.ru/two'],limit=10,seconds=300))
    assert len(calls)==3 and all(b[1]-a[1]>=20 for a,b in zip(calls,calls[1:]))

def test_retry_after_503_stops_without_retry_and_retains_header():
    run,job,backend,calls,url=setup('kami',status=503)
    result=run()
    assert result['termination_reason']=='SOURCE_RETRY_AFTER_HTTP_503' and not backend.writes
    assert len(calls)==2 and result['request_metrics']['retry_count']==0
    records=[json.loads(p.read_text(encoding='utf-8')) for p in (job_dir('kami',1)/'responses').glob('*.json')]
    assert next(r for r in records if r['url']==url)['retry_after_seconds']==120

def test_discovery_continues_nodes_after_page_budget_without_repeating_visited():
    run,job,backend,_,url=setup('vekprom','DISCOVERY')
    job.settings_snapshot['crawl_settings']['max_discovery_pages']=1
    from test_source_jobs import saved_case
    body,_,charset,_=saved_case('vekprom');clock=Clock();calls=[]
    child='https://vekprom.ru/sitemap-iblock-1.xml';root=root_urls('vekprom')[0]
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            validator(target);self.before_request(target);calls.append(target)
            if target.endswith('robots.txt'):return HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /')
            if target==root:return HttpResponse(target,200,'application/xml',f'<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>'.encode())
            if target==child:return HttpResponse(target,200,'application/xml',f'<urlset><url><loc>{url}</loc></url></urlset>'.encode())
            return HttpResponse(target,200,'text/html; charset='+charset,body)
    def execute(j):
        token=LEASED_HOST.set(j.domain)
        try:return run_source(j,None,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
        finally:LEASED_HOST.reset(token)
    first=execute(job)
    assert first['termination_reason']=='DISCOVERY_PAGE_BUDGET' and first['resume_available'] and not backend.writes
    resumed=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    second=execute(resumed)
    assert second['status']=='succeeded' and second['sql_committed']==1
    assert calls.count(root)==calls.count(child)==calls.count(url)==1

def test_actual_two_worker_runtime_overlap_and_shared_start_gate(monkeypatch):
    from test_source_jobs import saved_case
    run,job,backend,_,url=setup('kami')
    raw,_,charset,_=saved_case('kami');second=url.rstrip('/')+'-second/'
    body2=raw.replace(url.encode(),second.encode()).replace(b'9807314',b'9807315')
    rate=DomainRatePolicy(max_concurrency=2,min_request_interval_seconds=.03,jitter_min_seconds=.001,jitter_max_seconds=.003,max_attempts=1,request_timeout_seconds=20,max_run_duration_seconds=300)
    approval={'host':'stanki.ru','reference':'OFFLINE_FAKE_TRANSPORT_ONLY'}
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({'stanki.ru':policy_digest('kami','stanki.ru',rate,approval['reference'])}))
    job.settings_snapshot['rate_settings']=rate.snapshot();job.settings_snapshot['source_input']['rate_policy']=rate.snapshot()
    job.settings_snapshot['source_input']['entries'].append({'url':second,'source_external_id':'site:9807315'})
    job.settings_snapshot['crawl_settings']['host_policy_approval']=approval
    lock=threading.Lock();starts=[];active=0;maximum=0
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            nonlocal active,maximum
            validator(target);self.before_request(target)
            with lock:starts.append(time.perf_counter())
            if target.endswith('robots.txt'):return HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /')
            with lock:active+=1;maximum=max(maximum,active)
            time.sleep(.09)
            with lock:active-=1
            return HttpResponse(target,200,'text/html; charset='+charset,raw if target==url else body2)
    class PreciseClock:
        monotonic=staticmethod(time.perf_counter);sleep=staticmethod(time.sleep)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,None,backend_factory=lambda *a:backend,client_factory=Client,clock=PreciseClock())
    finally:LEASED_HOST.reset(token)
    assert result['status']=='succeeded' and result['sql_committed']==2,result
    assert maximum==2 and all(b-a>=.029 for a,b in zip(starts,starts[1:]))

def test_engine_change_blocks_resume_before_backend_or_http():
    run,job,backend,calls,url=setup('kami');job.settings_snapshot['source_input']['engine_sha256']='0'*64
    from test_source_jobs import deny
    token=LEASED_HOST.set(job.domain)
    try:
        with pytest.raises(ValueError,match='ENGINE_CHANGED'):run_source(job,None,backend_factory=deny,client_factory=deny)
    finally:LEASED_HOST.reset(token)
    assert not calls
