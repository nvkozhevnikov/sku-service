"""Offline grant/clock/queue recovery/source isolation. All network and SQL are doubles."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib,json
import pytest
from universal_supplier.control_plane import automation_execution as ex
from universal_supplier.control_plane.source_canary import TARGET
from universal_supplier.control_plane.source_jobs import RUNTIME_SOURCES
from universal_supplier.control_plane.supplier_automation import settings_for,digest
from universal_supplier.control_plane.queue import EnqueueResult

NOW=datetime.now(timezone.utc)

@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    def denied(*a,**k):raise AssertionError('OFFLINE_NETWORK_AND_SQL_DENIED')
    monkeypatch.setattr('psycopg.connect',denied)
    monkeypatch.setattr('requests.Session.send',denied)
    monkeypatch.setattr(ex,'ACTIVE',None)

def grant():
    return {'format':'supplier-schedule-grant/v1','head':'approved','engine':'engine',
        'target':TARGET,'not_before':(NOW-timedelta(minutes=1)).isoformat(),
        'execution_until':(NOW+timedelta(hours=23)).isoformat(),'max_parallel':2,
        'suppliers':{code:{'supplier_id':i,'host':code+'.test','limits':{'REFRESH':30000,'DISCOVERY':30000},
            'settings_sha256':digest(settings_for({})),
            'rate_sha256':'rate','enabled_at_approval':True,'min_request_interval_seconds':1,
            'max_queue_attempts':3,'max_discovery_pages':100,'max_run_duration_seconds':300}
            for i,code in enumerate(RUNTIME_SOURCES,1)},'host_policies':{},
        'mappings1282':[[i,'source',str(i),i,str(i)] for i in range(1,1283)],
        'protected_live11_batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d',
        'production':False,'esol':False}

def write(tmp_path,value):
    p=tmp_path/'grant.json';p.write_text(json.dumps(value),encoding='utf-8')
    return p,hashlib.sha256(p.read_bytes()).hexdigest()

def mocked_identity(monkeypatch,*,dirty=False):
    monkeypatch.setattr(ex.subprocess,'check_output',lambda args,**k:'dirty' if args[1]=='status' and dirty else '' if args[1]=='status' else 'approved')
    monkeypatch.setattr('universal_supplier.control_plane.source_runtime.engine_fingerprint',lambda:'engine')

def test_exact_approval_not_inferred_and_dirty_checkout_stops(tmp_path,monkeypatch):
    p,sha=write(tmp_path,grant());mocked_identity(monkeypatch)
    assert len(ex.load(p,sha)['suppliers'])==11
    with pytest.raises(PermissionError,match='EXACT_OPERATOR'):ex.load(p,sha,for_execution=True)
    assert ex.load(p,sha,sha,for_execution=True)['max_parallel']==2
    mocked_identity(monkeypatch,dirty=True)
    with pytest.raises(PermissionError,match='DIRTY'):ex.load(p,sha,sha,for_execution=True)
    with pytest.raises(PermissionError,match='SHA_CHANGED'):ex.load(p,'0'*64)


def test_ca_bundle_is_part_of_exact_execution_approval(tmp_path,monkeypatch):
    v=grant();v['transport_tls']={'format':'public-certifi-tls/v1','ca_bundle_sha256':'a'*64,
        'verify_mode':'CERT_REQUIRED','check_hostname':True}
    p,sha=write(tmp_path,v);mocked_identity(monkeypatch)
    monkeypatch.setattr('universal_supplier.public_connection.public_tls_identity',lambda:dict(v['transport_tls']))
    assert ex.load(p,sha)['transport_tls']['check_hostname']
    monkeypatch.setattr('universal_supplier.public_connection.public_tls_identity',lambda:{**v['transport_tls'],'ca_bundle_sha256':'b'*64})
    with pytest.raises(PermissionError,match='TLS_POLICY_CHANGED'):ex.load(p,sha)

@pytest.mark.parametrize('field,value,reason',[('max_parallel',3,'MAX2'),('head','other','HEAD'),
    ('engine','other','ENGINE'),('production',True,'PROTECTED'),('esol',True,'PROTECTED'),
    ('target',{'port':55449},'TARGET'),('execution_until',(NOW+timedelta(days=2)).isoformat(),'WINDOW'),
    ('mappings1282',[[1]]*1282,'MAPPING')])
def test_approval_drift_stops_before_any_io(tmp_path,monkeypatch,field,value,reason):
    v=grant();v[field]=value;p,sha=write(tmp_path,v);mocked_identity(monkeypatch)
    with pytest.raises(PermissionError,match=reason):ex.load(p,sha)

def test_session_monotonic_deadline_stop_file_and_head_drift(tmp_path,monkeypatch):
    value=grant();p,sha=write(tmp_path,value);mocked_identity(monkeypatch)
    session=ex.Session(None,value,sha,tmp_path,approval_path=p)
    monkeypatch.setattr(ex.time,'monotonic',lambda:session.started_mono+session.seconds+1)
    assert session.reason()=='AUTOMATION_EXECUTION_WINDOW_EXPIRED'
    assert session.end==session.hard-timedelta(minutes=5)
    session.failure=None;session.seconds=10**9
    monkeypatch.setattr(ex.time,'monotonic',lambda:session.started_mono)
    (tmp_path/'STOP_REQUESTED').write_text('OPERATOR_STOP')
    assert session.reason()=='OPERATOR_STOP'

def test_advisory_lock_and_unfinished_live11_fail_closed(tmp_path,monkeypatch):
    mocked_identity(monkeypatch);monkeypatch.setattr('universal_supplier.control_plane.source_canary.validate_config',lambda c:None)
    monkeypatch.setattr('universal_supplier.control_plane.source_canary.validate_connection',lambda c:None)
    p,sha=write(tmp_path,grant());session=ex.Session(None,grant(),sha,tmp_path,approval_path=p)
    session.config=SimpleNamespace(kwargs=lambda:{})
    monkeypatch.setattr(session,'check',lambda:None);monkeypatch.setattr(session,'check_mappings',lambda c:None)
    class Connection:
        closed=False
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args=None):self.sql=sql;return self
        def fetchone(self):return ('rc_runtime',False,False) if 'current_user' in self.sql else (True,)
        def fetchall(self):return [('partial',)]*22 if 'DISTINCT ON' in self.sql else []
    monkeypatch.setattr('psycopg.connect',lambda **k:Connection())
    with pytest.raises(PermissionError,match='LIVE11_UNFINISHED'):
        with session.activate():raise AssertionError('MUST_NOT_START')
    assert ex.ACTIVE is None and not (tmp_path/'SESSION.json').exists()

def test_same_host_limiter_is_reused_across_source_jobs(tmp_path):
    from test_host_fetch import Clock
    from universal_supplier.control_plane.rate_control import DomainRatePolicy
    v=grant();v['suppliers']['kami']['host']=v['suppliers']['vekprom']['host']='shared.test'
    v['suppliers']['kami']['min_request_interval_seconds']=3
    p,sha=write(tmp_path,v);s=ex.Session(None,v,sha,tmp_path,approval_path=p);clock=Clock()
    rate=DomainRatePolicy.from_mapping({'min_request_interval_seconds':1,'jitter_max_seconds':0})
    first=s.host_limiter('shared.test',rate,clock);first.wait();at=clock.monotonic()
    second=s.host_limiter('shared.test',rate,clock);second.wait()
    assert first is second and clock.monotonic()-at>=3

class ScheduleDB:
    def __init__(self,previous=None,*,enabled=True):self.previous=previous;self.enqueued=[];self.busy=False
    def connect(self):return self
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def cursor(self):return self
    def execute(self,sql,args=None):self.sql=sql
    def fetchall(self):
        if 'DISTINCT ON' in self.sql:
            return [(self.previous[1],self.previous[4],self.previous[6],self.previous[5])] if self.previous else []
        cfg=settings_for({});cfg.update(schedule_enabled=True,refresh_enabled=True,discovery_enabled=True,start_time=NOW.strftime('%H:%M'))
        return [(1,'kami',True,{}, {'automation':cfg})]
    def fetchone(self):
        return (1,) if 'SELECT id FROM' in self.sql and self.busy else None if 'SELECT id FROM' in self.sql else self.previous
    def preview_source_canary(self,*a):return {'confirmation_sha256':'preview'}
    def enqueue(self,sid,**kw):self.enqueued.append(kw);self.busy=True;return EnqueueResult(99,'queued')
    def source_resume_status(self,jid):self.proven=jid

def schedule(monkeypatch,db):
    from universal_supplier.control_plane.scheduler import Scheduler
    v=grant();session=SimpleNamespace(sha='approved',value=v,check=lambda:None,fail=lambda reason: (_ for _ in ()).throw(PermissionError(reason)))
    v['suppliers']['kami'].pop('settings_sha256')  # DB double tests scheduler branches; full approval checks are separate.
    monkeypatch.setattr(ex,'require_active',lambda sha:session)
    s=Scheduler(None,db,automation=session);s._connect=db.connect
    return s

def test_schedule_restart_dedup_independent_modes_and_native_resume(monkeypatch):
    db=ScheduleDB();s=schedule(monkeypatch,db)
    assert s.tick(now=NOW)==[99] and db.enqueued[0]['source_mode']=='REFRESH'
    assert s.tick(now=NOW)==[]  # Same durable queue intent, no duplicate.
    previous=(12,'partial',30,NOW,{'automation':{'approval_sha256':'approved'}},None,
        {'source_runtime':{'resume_available':True,'resume_uncertain':[]}})
    db=ScheduleDB(previous);s=schedule(monkeypatch,db)
    assert s.tick(now=NOW)==[99] and db.proven==12
    assert db.enqueued[0]['source_mode']=='RESUME' and db.enqueued[0]['resume_job_id']==12

def test_legacy_batch_and_uncertain_parent_not_automatically_restarted(monkeypatch):
    db=ScheduleDB((12,'partial',30,NOW,{'source_batch':{'id':'old'}},None,{}));s=schedule(monkeypatch,db)
    assert s.tick(now=NOW)==[] and not db.enqueued
    assert 'LIVE11_REQUIRES' in s.blocked['kami']
    db=ScheduleDB((12,'partial',30,NOW,{'automation':{'approval_sha256':'approved'}},None,
        {'source_runtime':{'resume_available':False,'resume_uncertain':['url'],'termination_reason':'UNCERTAIN'}}))
    s=schedule(monkeypatch,db)
    with pytest.raises(PermissionError,match='CRITICAL_JOB'):s.tick(now=NOW)
    assert not db.enqueued

def test_source_403_blocks_source_without_global_retry(monkeypatch):
    db=ScheduleDB((12,'partial',30,NOW,{'automation':{'approval_sha256':'approved'}},None,
        {'source_runtime':{'resume_available':False,'termination_reason':'SOURCE_BLOCKED_HTTP_403'}}));s=schedule(monkeypatch,db)
    assert s.tick(now=NOW)==[] and s.blocked=={'kami':'SOURCE_BLOCKED_HTTP_403'} and not db.enqueued

def test_local_hourly_and_stop_reports_are_atomic_not_checkpoint_replacements(tmp_path):
    from universal_supplier.control_plane.source_hourly import save_report,checkpoint
    from universal_supplier.control_plane.automation_status import overview,stop
    f=tmp_path/'one';f.mkdir();(f/'SESSION.json').write_text('{}')
    report={'at':NOW.isoformat(),'postgres':'IDENTITY_VERIFIED','worker':'RUNNING','original1282_exact':True,
        'sources':[{'source':s,'committed':i,'unique_products':i,'AUTO':0,'REVIEW':0,'CONFLICT':0,'NEW':i}
         for i,s in enumerate(RUNTIME_SOURCES)]}
    save_report(f/'hourly',report,0);save_report(f/'hourly',{**report,'at':(NOW+timedelta(hours=1)).isoformat()},1)
    assert len(list((f/'hourly').glob('*.json')))==3
    assert len((f/'hourly/latest.csv').read_text().splitlines())==12
    assert overview(tmp_path)[0]['latest']['original1282_exact']
    stop('one',tmp_path);assert (f/'STOP_REQUESTED').read_text()=='OPERATOR_STOP'
    with pytest.raises(ValueError):stop('../escape',tmp_path)
    assert not (f/'checkpoint.json').exists()

def test_auth_schema_missing_fails_closed_without_delete(monkeypatch):
    from universal_supplier.control_plane.security import PostgresLoginThrottle
    class Cursor:
        def execute(self,*a):pass
        def fetchone(self):return (False,)
    with pytest.raises(PermissionError,match='SCHEMA018'):PostgresLoginThrottle._schema(Cursor())

def test_suppliers_explicit_disabled_approval_cannot_be_activated(monkeypatch):
    v=grant();v['suppliers']['kami']['enabled_at_approval']=False
    monkeypatch.setattr(ex,'require_active',lambda sha:SimpleNamespace(value=v))
    with pytest.raises(PermissionError,match='ENABLE_REQUIRES'):
        ex.validate_job({'automation':{'approval_sha256':'sha'},'supplier_code':'kami'})

@pytest.mark.parametrize('code',RUNTIME_SOURCES)
def test_all11_scheduled_native_checkpoint_resume_does_not_repeat_committed(monkeypatch,tmp_path,code):
    from dataclasses import replace
    from test_source_runtime import setup
    from universal_supplier.control_plane.source_runtime import engine_fingerprint
    from test_partner_pilot import CFG
    from universal_supplier.control_plane.source_canary import build_preview
    from universal_supplier.control_plane.source_discovery import HOSTS
    from universal_supplier.control_plane.host_fetch import host_key
    from universal_supplier.control_plane.rate_control import DomainRateLimiter
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'e'))
    for flag in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):
        monkeypatch.setenv(flag,'YES')
    run,job,backend,calls,url=setup(code,config=CFG)
    source=job.settings_snapshot['source_input'];entry=source['entries'][0]
    policy=job.settings_snapshot['rate_settings'];crawl=job.settings_snapshot['crawl_settings']
    preview=build_preview(1,(code,code,'https://'+HOSTS[code],True,crawl,policy),
        [(1,entry['source_external_id'],url,1)],CFG,1)
    snap=preview['snapshot'];value=grant()
    value['suppliers'][code].update(supplier_id=1,rate_sha256=digest(policy),limits={'REFRESH':1,'DISCOVERY':1},
        max_discovery_pages=snap['source_input']['canary']['max_discovery_pages'],
        max_run_duration_seconds=snap['source_input']['canary']['max_run_duration_seconds'],host=snap['domain'])
    value['suppliers'][code]['settings_sha256']=digest(settings_for(snap))
    fake=SimpleNamespace(sha='approved',value=value,check=lambda:None,reason=lambda:None,
        host_limiter=lambda host,rate,clock:DomainRateLimiter(rate,clock=clock))
    monkeypatch.setattr(ex,'ACTIVE',fake)
    snap['automation']={'approval_sha256':'approved','phase':'REFRESH','rate_sha256':digest(policy)}
    scheduled=replace(job,trigger_type='schedule',settings_snapshot=snap)
    first=run(scheduled);assert first['status']=='succeeded',first
    before=list(calls)
    resumed=replace(scheduled,id=2,settings_snapshot={**snap,'crawl_settings':{**snap['crawl_settings'],
        'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    second=run(resumed)
    assert second['status']=='succeeded' and second['sql_committed']==1
    assert calls==before and len(backend.writes)==1 and len(backend.matches)==1

def test_bounded_host_uses_existing_loops_hourly_reports_and_restores_environment(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from universal_supplier.control_plane import scheduler_main as main
    calls=[];value=grant();monkeypatch.setattr(ex,'load',lambda *a,**k:value)
    class Session:
        def __init__(self,*a,**k):self.n=0
        def check(self):
            self.n+=1
            if self.n==3:raise PermissionError('OPERATOR_STOP')
        def fail(self,reason):calls.append(reason)
        @contextmanager
        def activate(self):yield self
    @contextmanager
    def awake():
        calls.append('awake')
        try:yield
        finally:calls.append('sleep_request_released')
    class Queue:
        def __init__(self,*a,**k):pass
        def _connect(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self
        def execute(self,*a):pass
        def fetchall(self):return []
        def stop_automation(self,sha):calls.append('queue_stopped')
    class Worker:
        def __init__(self,*a,**k):self.pool=SimpleNamespace(shutdown=lambda **k:calls.append('workers_drained'))
        def tick(self):calls.append('existing_worker_tick')
    class Scheduler:
        blocked={}
        def __init__(self,*a,**k):pass
        def tick(self):calls.append('existing_scheduler_tick')
    monkeypatch.setattr(ex,'Session',Session);monkeypatch.setattr(main,'PostgresJobQueue',Queue)
    monkeypatch.setattr(main,'Orchestrator',Worker);monkeypatch.setattr(main,'Scheduler',Scheduler)
    monkeypatch.setattr('universal_supplier.control_plane.source_autonomy.keep_awake',awake)
    clock=[0]
    def monotonic():clock[0]+=3601;return clock[0]
    monkeypatch.setattr(ex.time,'monotonic',monotonic);monkeypatch.setattr(ex.time,'sleep',lambda _:None)
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','NO')
    reports=[]
    def reporter(session,index,**kw):
        reports.append(index);return {'at':NOW.isoformat(),'active_jobs':[],'original1282_exact':True}
    result=main.run_approved(None,tmp_path/'approval','sha','sha',folder=tmp_path,reporter=reporter)
    assert reports==[0,1,2,3] and result['reason']=='OPERATOR_STOP'
    assert 'queue_stopped' in calls and 'workers_drained' in calls and calls[-1]=='sleep_request_released'
    assert __import__('os').environ['STERBRUST_ALLOW_SUPPLIER_HTTP']=='NO'
    assert json.loads((tmp_path/'FINAL.json').read_text())['active_jobs']==[]

def test_unknown_transport_keeps_uncertain_checkpoint_and_never_ingests_or_matches(tmp_path,monkeypatch):
    from test_source_canary import FakeDB
    from test_partner_pilot import CFG
    from test_source_runtime import Backend
    from test_host_fetch import Clock
    from universal_supplier.control_plane.queue import CrawlJob
    from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST
    from universal_supplier.control_plane.rate_control import DomainRateLimiter
    from universal_supplier.http_capture import HttpResponse
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'e'))
    for key in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):
        monkeypatch.setenv(key,'YES')
    db=FakeDB();snap=db.queue().preview_source_canary(11,1)['snapshot'];value=grant();canary=snap['source_input']['canary']
    rate=snap['rate_settings'];value['suppliers']['beka_mak'].update(supplier_id=11,host=snap['domain'],
        limits={'REFRESH':1,'DISCOVERY':1},rate_sha256=digest(rate),settings_sha256=digest(settings_for(snap)),
        max_run_duration_seconds=canary['max_run_duration_seconds'],max_discovery_pages=canary['max_discovery_pages'])
    failure=[]
    session=SimpleNamespace(sha='approved',value=value,check=lambda:None,reason=lambda:failure[0] if failure else None,
        fail=lambda r:failure.append(r),host_limiter=lambda host,rate,clock:DomainRateLimiter(rate,clock=clock))
    monkeypatch.setattr(ex,'ACTIVE',session)
    snap['automation']={'approval_sha256':'approved','phase':'REFRESH','rate_sha256':digest(rate)}
    calls=[]
    class Client:
        def get_validated(self,url,**kw):
            self.before_request(url);calls.append(url)
            if url.endswith('/robots.txt'):return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /')
            raise TimeoutError('outcome unknown')
    backend=Backend();job=CrawlJob(44,11,'beka_mak','beka_mak',db.supplier[2],'schedule','full','running',NOW,snap)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=Client,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['status']=='partial' and result['termination_reason']=='AUTOMATION_UNKNOWN_HTTP_OUTCOME'
    assert len(calls)==2 and result['request_metrics']['retry_count']==0 and result['sql_committed']==0
    assert not backend.writes and not backend.matches and failure==['AUTOMATION_UNKNOWN_HTTP_OUTCOME']
    cp=json.loads(Path(result['checkpoint']).read_text())
    assert cp['terminal'][snap['source_input']['entries'][0]['url']]['state']=='UNCERTAIN'

@pytest.mark.parametrize('system,role',[('7698346631476390904',('rc_runtime',False,False)),
    ('wrong',('rc_runtime',False,False)),('7698346631476390904',('rc_runtime',True,False)),
    ('7698346631476390904',('unknown_role',False,False)),('7698346631476390904',('rc_runtime',False,True))])
def test_guarded_ui_and_queue_connections_check_identity_and_role_each_time(monkeypatch,system,role):
    from dataclasses import replace
    from test_partner_pilot import CFG
    from universal_supplier.control_plane.runtime_config import guarded_connect
    class Connection:
        closed=False
        def cursor(self,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,args=None):self.query=query
        def fetchone(self):
            return (TARGET['dbname'],system,'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port']) if 'pg_control_system' in self.query else role
        def close(self):self.closed=True
    connection=Connection();monkeypatch.setattr('psycopg.connect',lambda **kw:connection)
    cfg=replace(CFG,user='rc_runtime')
    if system=='7698346631476390904' and role==('rc_runtime',False,False):
        assert guarded_connect(cfg,autocommit=True) is connection and not connection.closed
    else:
        with pytest.raises(PermissionError):guarded_connect(cfg,autocommit=True)
        assert connection.closed

def test_queue_attempts_cannot_reset_when_postgres_returns_a_different_timezone(monkeypatch):
    from test_source_canary import FakeDB
    db=FakeDB();original_connect=db.connect;rows=[]
    for flag in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):
        monkeypatch.setenv(flag,'YES')
    class Connection:
        def __init__(self):self.base=original_connect()
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def transaction(self):return nullcontext()
        def cursor(self):return Cursor(self.base.cursor())
    class Cursor:
        def __init__(self,base):self.base=base
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args=None):
            self.sql=sql;self.args=args;self.base.execute(sql,args)
            if 'INSERT INTO crawl_jobs' in sql:rows.append((args[3],json.loads(args[4])))
        def fetchone(self):
            if 'SELECT count(*) FROM crawl_jobs' in self.sql:
                occurrence=self.args[-1]
                # PostgreSQL timestamptz compares instants; JSON text compares spellings.
                return (sum(t==occurrence if isinstance(occurrence,datetime) else s['automation']['scheduled_for']==occurrence for t,s in rows),)
            return self.base.fetchone()
        def fetchall(self):return self.base.fetchall()
    q=db.queue();q._connect=Connection
    preview=q.preview_source_canary(11,109);snap=preview['snapshot'];v=grant();cap=snap['source_input']['canary']
    v['suppliers']['beka_mak'].update(supplier_id=11,host=snap['domain'],rate_sha256=digest(snap['rate_settings']),
        settings_sha256=digest(settings_for(snap)),limits={'REFRESH':109,'DISCOVERY':109},
        max_discovery_pages=cap['max_discovery_pages'],max_run_duration_seconds=cap['max_run_duration_seconds'])
    monkeypatch.setattr(ex,'ACTIVE',SimpleNamespace(sha='approved',value=v,check=lambda:None))
    for offset in (0,3,4):
        result=q.enqueue(11,trigger_type='schedule',crawl_type='full',source_mode='REFRESH',source_limit=109,
            source_preview_sha256=preview['confirmation_sha256'],automation_sha256='approved',
            scheduled_for=NOW.astimezone(timezone(timedelta(hours=offset))))
        assert result.enqueued
    with pytest.raises(PermissionError,match='ATTEMPTS_EXHAUSTED'):
        q.enqueue(11,trigger_type='schedule',crawl_type='full',source_mode='REFRESH',source_limit=109,
            source_preview_sha256=preview['confirmation_sha256'],automation_sha256='approved',
            scheduled_for=NOW.astimezone(timezone(timedelta(hours=5))))
    assert len(rows)==3 and len({s['automation']['scheduled_for'] for t,s in rows})==1
