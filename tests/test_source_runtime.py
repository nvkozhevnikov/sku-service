"""Actual nine parsers + host fetch + checkpoint; fake network/SQL only."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import json,socket,threading
from unittest.mock import patch
import pytest
from test_source_jobs import saved_case,job_for,deny
from test_host_fetch import Clock
from universal_supplier.control_plane.source_jobs import SOURCES,make_source_runner,parse_existing
from universal_supplier.control_plane.source_runtime import LEASED_HOST,run_source,job_dir,engine_fingerprint
from universal_supplier.control_plane.source_discovery import HOSTS,root_urls
from universal_supplier.control_plane.host_fetch import compile_policy
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.http_capture import HttpResponse

class Backend:
    def __init__(self):self.saved={};self.writes=[];self.matches=[];self.summaries=[];self.fail=None
    def start(self,job,mode):return 80+job.id
    def persist(self,product,capture):
        if self.fail=='before':raise RuntimeError('TEST_INTERRUPT_BEFORE_COMMIT')
        self.saved[capture.fingerprint]=capture;self.writes.append(capture)
        if self.fail=='after':raise RuntimeError('TEST_INTERRUPT_AFTER_COMMIT')
        return {'exact_noop':False}
    def probe(self,entry,receipt):return 'COMMITTED' if receipt and receipt.get('capture_fingerprint') in self.saved else 'NOT_FOUND' if receipt else 'UNKNOWN'
    def matching(self,ids):
        self.matches.append(ids);return {'total':len(ids),'status':'succeeded','matching_applied':True,'f01_f03_enforced':True,'existing_ids_preserved':True}
    def progress(self,s):self.summaries.append(s)
    def finish(self,s):self.result=s
    def close(self):pass

@pytest.fixture(autouse=True)
def isolation(monkeypatch,tmp_path):
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'getaddrinfo',deny)
    monkeypatch.setattr('psycopg.connect',deny)

def setup(code,mode='REFRESH',*,status=200,robots='User-agent: *\nAllow: /\n',backend=None):
    if code=='partner_st':
        from test_partner_pilot import html
        body,url,charset,_=html(1).encode(),'https://partner-st.ru/item-1/','utf-8',None
    elif code=='optimum':
        from test_optimum_source import HTML,URL
        body,url,charset,_=HTML.encode(),URL,'utf-8',None
    else:body,url,charset,_=saved_case(code)
    clock=Clock();calls=[];backend=backend or Backend()
    from universal_supplier.ltt_uss_capture import sanitise_for_charset
    clean=sanitise_for_charset(body,charset)
    product=parse_existing(code,clean.decode(),url);ext=product.external_id if code in ('kami','partner_st','optimum') else product.site_internal_id
    rate=compile_policy(code,'https://'+HOSTS[code],{'jitter_max_seconds':0})
    value={'format':'source-runtime-input/v1','supplier_code':code,'mode':mode,
        'entries':[{'url':url,'source_external_id':ext}] if mode=='REFRESH' else [],
        'seeds':root_urls(code) if mode=='DISCOVERY' else [],'rate_policy':rate.snapshot(),'engine_sha256':engine_fingerprint()}
    job=CrawlJob(1,1,code,code,'https://'+HOSTS[code]+'/','manual','full','running',datetime.now(timezone.utc),
        {'source_input':value,'rate_settings':rate.snapshot(),'crawl_settings':{'collection_mode':mode,'persist_source_data':True,'max_products':5,'max_discovery_pages':5}})
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            validator(target);self.before_request(target);calls.append((target,clock.monotonic(),timeout_seconds))
            if target.endswith('/robots.txt'):return HttpResponse(target,200,'text/plain',robots.encode())
            if target in root_urls(code):
                content=(f'<div class="productCard"><a class="productCard__name" href="{url}">Machine</a></div>' if code=='kami' else f'<urlset><url><loc>{url}</loc></url></urlset>')
                return HttpResponse(target,200,'text/html' if code=='kami' else 'application/xml',content.encode())
            return HttpResponse(target,status,'text/html; charset='+charset,body,headers={'Retry-After':'120'})
    def run(j=job):
        token=LEASED_HOST.set(j.domain)
        try:
            if code in ('partner_st','optimum'):return run_source(j,None,client_factory=Client,backend_factory=lambda cfg,source:backend,clock=clock)
            return make_source_runner(code,transport_factory=Client,runtime_backend_factory=lambda cfg,source:backend,runtime_clock=clock)(j,None)
        finally:LEASED_HOST.reset(token)
    return run,job,backend,calls,url

@pytest.mark.parametrize('code',SOURCES)
@pytest.mark.parametrize('mode',['REFRESH','DISCOVERY'])
def test_all_nine_actual_parsers_discovery_capture_sql_and_matching(code,mode):
    run,job,backend,calls,url=setup(code,mode)
    result=run()
    assert result['status']=='succeeded',result
    assert result['sql_committed']==1 and len(backend.writes)==1 and result['deactivation_allowed'] is False
    assert backend.matches==[[result['rows'][0]['external_id']]]
    assert result['identity_post_processing']['f01_f03_enforced']
    assert all(b[1]-a[1]>=20 for a,b in zip(calls,calls[1:]))
    assert result['request_metrics']['retry_count']==0
    assert Path(result['manifest']).is_file() and Path(result['checkpoint']).is_file()
    assert result['discovery_complete']==(mode=='DISCOVERY')
    checkpoint=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    receipt=checkpoint['terminal'][url]['receipt']
    assert receipt['sql_verified'] and receipt['original_capture_metadata']['observed_at']==backend.writes[0].observed_at.isoformat()

@pytest.mark.parametrize('code',SOURCES)
def test_committed_resume_never_regets_or_repersist(code):
    run,job,backend,calls,url=setup(code);first=run();before=list(calls)
    settings={**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}
    resumed=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':settings})
    result=run(resumed)
    assert result['status']=='succeeded',result
    assert calls==before and len(backend.writes)==1 and len(backend.matches)==1

@pytest.mark.parametrize('point',['before','after'])
def test_capture_reconciles_after_interrupted_commit_without_http_or_duplicate(point):
    backend=Backend();backend.fail=point;run,job,backend,calls,url=setup('kami',backend=backend)
    first=run();assert first['status']=='partial';before=list(calls)
    backend.fail=None
    resumed=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{**job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    result=run(resumed);assert result['status']=='succeeded',result
    assert calls==before and len(backend.writes)==1
    assert result['sql_committed']==1

@pytest.mark.parametrize('code',SOURCES)
@pytest.mark.parametrize('status',[403,429])
def test_source_blocks_stop_and_preserve_without_sql(code,status):
    run,job,backend,calls,url=setup(code,status=status);result=run()
    assert result['termination_reason']=='SOURCE_BLOCKED_HTTP_'+str(status)
    assert result['request_metrics']['retry_count']==0 and result['sql_committed']==0 and not backend.matches
    assert not result['resume_available'] and len(calls)==2

def test_real_lease_required_before_transport_or_backend():
    run,job,backend,calls,url=setup('vekprom')
    with pytest.raises(PermissionError,match='LEASE'):run_source(job,None,backend_factory=deny,client_factory=deny)
    assert not calls and not backend.writes

def test_robots_stricter_policy_stops_before_product():
    run,job,backend,calls,url=setup('kami',robots='User-agent: *\nCrawl-delay: 30\nAllow: /\n')
    result=run();assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and len(calls)==1 and not backend.writes

def test_no_sql_readback_means_no_committed_receipt():
    backend=Backend();backend.probe=lambda *a:'UNKNOWN'
    run,job,backend,calls,url=setup('kami',backend=backend);result=run()
    assert result['sql_committed']==0 and not result['resume_available']
    assert result['termination_reason']=='SQL_COMMIT_NOT_PROVEN' and not backend.matches

def test_first_stop_reason_and_no_network_when_operator_stopped():
    run,job,backend,calls,url=setup('vekprom');folder=job_dir('vekprom',1);folder.mkdir(parents=True)
    (folder/'STOP_REQUESTED').write_text('OPERATOR_MAINTENANCE',encoding='utf-8')
    result=run();assert result['termination_reason']=='OPERATOR_MAINTENANCE' and not calls and not backend.writes
