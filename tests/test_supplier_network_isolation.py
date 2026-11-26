"""Incident137/138 replay: real parsers/checkpoints, injected HTTP/SQL only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from urllib.error import URLError
from types import SimpleNamespace
import errno,json,socket,ssl
import pytest
from universal_supplier.control_plane import automation_execution as ex
from universal_supplier.control_plane.host_fetch import BoundedHostFetcher
from universal_supplier.control_plane.transport_diagnostics import exception_evidence,safe_message
from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST
from universal_supplier.control_plane.commercial_refresh import failure_scope
from universal_supplier.http_capture import HttpResponse
from test_source_runtime import Backend,setup
from test_source_jobs import saved_case
from test_host_fetch import Clock
from test_automation_execution import grant

@pytest.fixture(autouse=True)
def deny_live(monkeypatch,tmp_path):
    def deny(*a,**k):raise AssertionError('OFFLINE_NETWORK_SQL_DENIED')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'getaddrinfo',deny)
    monkeypatch.setattr('psycopg.connect',deny);monkeypatch.setattr('requests.Session.send',deny)
    monkeypatch.setattr('urllib.request.OpenerDirector.open',deny)
    for k in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):monkeypatch.setenv(k,'YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES','{}')
    monkeypatch.setattr(ex,'ACTIVE',None)

def session(tmp_path,monkeypatch):
    from universal_supplier.control_plane.source_discovery import HOSTS
    from universal_supplier.control_plane.host_fetch import host_key
    v=grant()
    for code,r in v['suppliers'].items():r['host']=host_key('https://'+HOSTS[code])
    s=ex.Session(None,v,'offline',tmp_path,approval_path=tmp_path/'not-a-live-approval')
    s.check=lambda:None
    monkeypatch.setattr(ex,'ACTIVE',s)
    return s

def runner(code,*,robots_error=None,robots_response=None,product_error=None,beka_ready=None,inter_failed=None):
    _,job,backend,_,url=setup(code)
    job=replace(job,settings_snapshot={**job.settings_snapshot,'automation':{'approval_sha256':'offline'}})
    if code=='partner_st':
        from test_partner_pilot import html
        body,charset=html(1).encode(),'utf-8'
    elif code=='optimum':
        from test_optimum_source import HTML
        body,charset=HTML.encode(),'utf-8'
    else:body,_,charset,_=saved_case(code)
    calls=[];clock=Clock()
    class Client:
        def get_validated(self,target,**kw):
            kw['validator'](target);self.before_request(target);calls.append(target)
            if target.endswith('/robots.txt'):
                if code=='beka_mak' and beka_ready:beka_ready.set()
                if code=='intervesp' and beka_ready:assert beka_ready.wait(10)
                if robots_error:
                    if inter_failed:inter_failed.set()
                    raise robots_error
                return robots_response or HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /')
            if code=='beka_mak' and inter_failed:assert inter_failed.wait(10)
            if product_error:raise product_error
            return HttpResponse(target,200,'text/html; charset='+charset,body)
    def run():
        token=LEASED_HOST.set(job.domain)
        try:return run_source(job,None,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
        finally:LEASED_HOST.reset(token)
    return run,backend,calls,url

def test_actual_beka200_intervesp_urlerror_other_sources_continue(tmp_path,monkeypatch):
    s=session(tmp_path,monkeypatch);ready=Event();failed=Event()
    beka,bb,bc,bu=runner('beka_mak',beka_ready=ready,inter_failed=failed)
    inter,ib,ic,iu=runner('intervesp',robots_error=URLError(ConnectionResetError(errno.ECONNRESET,'peer reset')),beka_ready=ready,inter_failed=failed)
    with ThreadPoolExecutor(2) as pool:
        b=pool.submit(beka);i=pool.submit(inter);b=b.result();i=i.result()
    assert b['status']=='succeeded' and b['sql_committed']==1 and len(bb.writes)==1
    assert i['termination_reason']=='SOURCE_ROBOTS_UNAVAILABLE' and i['stop_scope']=='supplier'
    assert not ib.writes and not ib.matches and ic==['https://intervesp.ru/robots.txt']
    assert s.reason() is None and s.source_reason('intervesp')=='SOURCE_ROBOTS_UNAVAILABLE'
    assert s.source_reason('beka_mak') is None and bu in bc and iu not in ic
    other,ob,oc,ou=runner('vekprom');r=other()
    assert r['status']=='succeeded' and r['sql_committed']==1 and ou in oc
    errors=list((tmp_path/'evidence/intervesp/job-1/transport-errors').glob('*.json'))
    assert len(errors)==1
    original=json.loads(errors[0].read_text())['diagnostics']['original_exception']
    assert original['type']=='URLError' and original['reason']['errno']==errno.ECONNRESET
    assert original['reason']['type']=='ConnectionResetError'

@pytest.mark.parametrize('code',['partner_st','optimum','beka_mak_tr','kami','ltt_msk','ltt_sib','ltt_yug','uss_stanko','vekprom'])
def test_each_other_source_ignores_an_independent_robots_pause(tmp_path,monkeypatch,code):
    s=session(tmp_path,monkeypatch);s.pause_source('intervesp','SOURCE_ROBOTS_UNAVAILABLE')
    run,b,c,u=runner(code);r=run()
    assert r['status']=='succeeded' and r['sql_committed']==1 and u in c
    assert s.reason() is None

@pytest.mark.parametrize('cause',[URLError('network unavailable'),URLError(ssl.SSLCertVerificationError(1,'certificate verify failed')),TimeoutError('timeout')])
def test_robots_failure_has_no_retries_no_product_and_durable_details(tmp_path,monkeypatch,cause):
    s=session(tmp_path,monkeypatch);run,b,c,u=runner('intervesp',robots_error=cause)
    r=run()
    assert r['stop_scope']=='supplier' and len(c)==1 and u not in c
    assert r['request_metrics']['retry_count']==0 and r['transport_errors'][0]['request_scope']=='robots'
    assert r['transport_errors'][0]['original_exception']['type']==type(cause).__name__
    assert s.reason() is None

def test_product_unknown_still_stops_every_host_and_never_retries(tmp_path,monkeypatch):
    s=session(tmp_path,monkeypatch);run,b,c,u=runner('beka_mak',product_error=URLError(TimeoutError('unknown product outcome')))
    r=run()
    assert r['termination_reason']=='AUTOMATION_UNKNOWN_HTTP_OUTCOME' and len(c)==2 and not b.writes
    assert s.reason()=='AUTOMATION_UNKNOWN_HTTP_OUTCOME'
    assert r['transport_errors'][0]['stop_scope']=='system'
    other,ob,oc,ou=runner('vekprom');o=other()
    assert ou not in oc and not ob.writes

def test_common_permission_failure_is_not_hidden_as_supplier_network_problem(tmp_path,monkeypatch):
    s=session(tmp_path,monkeypatch);run,b,c,u=runner('intervesp',robots_error=URLError(PermissionError(errno.EACCES,'permission denied')))
    r=run()
    assert r['termination_reason']=='AUTOMATION_UNKNOWN_HTTP_OUTCOME' and s.reason()
    assert not s.paused_sources

def test_exception_chain_errno_and_secrets_are_preserved_or_redacted():
    low=ConnectionRefusedError(errno.ECONNREFUSED,'connect failed https://alice:PASS_SENTINEL@intervesp.ru/robots.txt?token=TOKEN_SENTINEL password=PWD_SENTINEL')
    e=URLError(low);e.__cause__=low
    evidence=exception_evidence(e);raw=json.dumps(evidence)
    assert evidence['original_exception']['type']=='URLError'
    assert evidence['original_exception']['reason']['errno']==errno.ECONNREFUSED
    assert 'intervesp.ru/robots.txt' in raw and not any(x in raw for x in ('PASS_SENTINEL','TOKEN_SENTINEL','PWD_SENTINEL','alice'))
    assert 'SECRET_HEADER' not in safe_message('Authorization: Bearer SECRET_HEADER')

def test_archive_failure_is_systemic_and_no_next_source_is_allowed(tmp_path,monkeypatch):
    s=session(tmp_path,monkeypatch)
    from universal_supplier.control_plane import source_runtime
    original=source_runtime.frozen_json
    def broken(path,value):
        if 'transport-errors' in str(path):raise OSError(errno.ENOSPC,'disk full')
        return original(path,value)
    monkeypatch.setattr(source_runtime,'frozen_json',broken)
    run,b,c,u=runner('intervesp',robots_error=URLError('unavailable'));r=run()
    assert s.reason()=='AUTOMATION_TRANSPORT_EVIDENCE_FAILED' and not b.writes
    assert r['stop_scope']=='system' and r['global_stop_reason']==s.reason()


@pytest.mark.parametrize('status,body',[(404,b'not found'),(200,b'<html>captcha</html>'),
    (200,b'User-agent: *\nCrawl-delay: 600\nAllow: /')])
def test_unproven_or_restrictive_robots_is_never_bypassed(tmp_path,monkeypatch,status,body):
    s=session(tmp_path,monkeypatch)
    run,b,c,u=runner('intervesp',robots_response=HttpResponse('https://intervesp.ru/robots.txt',status,'text/plain',body))
    r=run()
    assert r['stop_scope']=='supplier' and s.reason() is None
    assert u not in c and not b.writes and s.source_reason('intervesp')
    other,ob,oc,ou=runner('vekprom');assert other()['status']=='succeeded'


def test_pausing_one_shared_host_pauses_its_sources_only(tmp_path,monkeypatch):
    s=session(tmp_path,monkeypatch)
    s.value['suppliers']['beka_mak_tr']['host']=s.value['suppliers']['beka_mak']['host']
    s.pause_source('beka_mak','SOURCE_ROBOTS_UNAVAILABLE')
    assert s.source_reason('beka_mak_tr') and not s.source_reason('intervesp') and s.reason() is None

@pytest.mark.parametrize('reason,rt,expected',[
    ('SOURCE_ROBOTS_UNAVAILABLE',{},'supplier'),('SOURCE_BLOCKED_HTTP_429',{},'supplier'),
    ('SOURCE_CARD_ERROR_THRESHOLD',{},'supplier'),('SQL_READBACK_MISMATCH',{},'system'),
    ('SOURCE_UPDATE_MAPPING_CONFLICT',{},'system'),('AUTOMATION_UNKNOWN_HTTP_OUTCOME',{},'system'),
    ('permission denied for table offers',{},'system'),('unrecognized exception',{},'system'),
    ('SOURCE_ROBOTS_UNAVAILABLE',{'resume_uncertain':['product']},'system'),
    ('SOURCE_ROBOTS_UNAVAILABLE',{'global_stop_reason':'AUTOMATION_TRANSPORT_EVIDENCE_FAILED'},'system'),
    ('MAX_RUN_DURATION_EXCEEDED',{'resume_available':True},'resume')])
def test_error_scope_is_a_fail_closed_allowlist(reason,rt,expected):assert failure_scope(reason,rt)==expected


def commercial_scheduler(tmp_path,monkeypatch,rt):
    from universal_supplier.control_plane import commercial_refresh as cr
    from universal_supplier.control_plane.scheduler import Scheduler
    s=session(tmp_path,monkeypatch)
    s.value['suppliers']={code:{**s.value['suppliers'][code],'supplier_id':sid,'targets':[{'frozen':True}]}
        for sid,code in enumerate(('intervesp','beka_mak','vekprom'),1)}
    class DB:
        calls=[];answer=None
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self
        def execute(self,sql,args):
            assert sql.lstrip().startswith('SELECT')
            self.answer=(138,'partial',51,{}, {'source_runtime':rt},None) if args[0]==1 else None
        def fetchone(self):return self.answer
        def preview_source_canary(self,*a):return {'confirmation_sha256':'frozen'}
        def enqueue(self,sid,**kw):self.calls.append((sid,kw));return SimpleNamespace(enqueued=True,job_id=200+sid)
        def source_resume_status(self,*a):raise AssertionError('BLOCKED_ROBOTS_MUST_NOT_RESUME')
    db=DB();scheduler=Scheduler(None,db,automation=s);scheduler._connect=lambda:db
    return cr,s,scheduler,db


def test_existing_commercial_scheduler_blocks_only_failed_robots_and_admits_other_frozen_sources(tmp_path,monkeypatch):
    cr,s,scheduler,db=commercial_scheduler(tmp_path,monkeypatch,{'termination_reason':'SOURCE_ROBOTS_UNAVAILABLE'})
    assert cr.tick(scheduler,None)==[202,203]
    assert scheduler.blocked=={'intervesp':'SOURCE_ROBOTS_UNAVAILABLE'} and s.reason() is None
    assert [sid for sid,kw in db.calls]==[2,3]
    assert all(kw['source_mode']=='REFRESH' for sid,kw in db.calls)


@pytest.mark.parametrize('rt',[{'termination_reason':'SQL_READBACK_MISMATCH'},
    {'termination_reason':'SOURCE_ROBOTS_UNAVAILABLE','resume_uncertain':['product']},
    {'termination_reason':'SOURCE_ROBOTS_UNAVAILABLE','global_stop_reason':'AUTOMATION_TRANSPORT_EVIDENCE_FAILED'}])
def test_commercial_scheduler_does_not_hide_shared_or_uncertain_failures(tmp_path,monkeypatch,rt):
    cr,s,scheduler,db=commercial_scheduler(tmp_path,monkeypatch,rt)
    s.check=lambda: (_ for _ in ()).throw(PermissionError(s.reason())) if s.reason() else None
    with pytest.raises(PermissionError,match='AUTOMATION_CRITICAL_JOB'):cr.tick(scheduler,None)
    assert not db.calls
