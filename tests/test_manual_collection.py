"""Authenticated button -> native queue contract. All transports/SQL are fakes."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import pytest
from test_automation_admin import client, csrf
from universal_supplier.control_plane import manual_collection
from universal_supplier.control_plane.manual_collection import ManualRequest, verify
from universal_supplier.control_plane.security import AuthUser
from universal_supplier.control_plane.host_fetch import MANUAL_POLICIES
from universal_supplier.control_plane.source_jobs import RUNTIME_SOURCES
from universal_supplier.control_plane.source_discovery import HOSTS
from universal_supplier.control_plane.source_canary import TARGET
from universal_supplier.control_plane.queue import PostgresJobQueue, EnqueueResult
from universal_supplier.control_plane.rate_control import DomainRatePolicy

KEY='isolated-test-signing-key-not-a-real-secret'


class DB:
    def __init__(self,code):self.code=code;self.snapshots=[];self.sql=[];self.enabled=True
    def connect(self):
        db=self
        class C:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def transaction(self):return nullcontext()
            def cursor(self):return Q()
        class Q:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,sql,args=None):
                self.sql=sql;self.args=args;db.sql.append((sql,args))
                if 'INSERT INTO crawl_jobs' in sql:db.snapshots.append(json.loads(args[4]))
                if 'UPDATE suppliers SET enabled=true' in sql:db.enabled=True
            def fetchone(self):
                if 'pg_control_system' in self.sql:return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
                if 'FROM suppliers' in self.sql:return (db.code,db.code,'https://'+HOSTS[db.code]+'/',db.enabled,{},DomainRatePolicy().snapshot())
                if 'SELECT id FROM crawl_jobs' in self.sql:return None
                return (44,)
            def fetchall(self):
                if 'catalog_product_id IS NOT NULL' in self.sql:return [(900,800)]
                if 'SELECT DISTINCT address.url' in self.sql:return []
                if 'WITH known AS' in self.sql:
                    path={'beka_mak':'/product/test-{i}/','beka_mak_tr':'/urun/model-{i}/en',
                          'vekprom':'/catalog/test/model-{i}/','ivstankomaster':'/item/test-{i}/'}.get(db.code,'/catalog/test-{i}/')
                    return [(i,str(1000+i),'https://'+HOSTS[db.code]+path.format(i=i),100) for i in range(1,self.args[1]+1)]
                raise AssertionError(self.sql)
        return C()
    def queue(self):
        from test_source_canary import CFG
        q=PostgresJobQueue(CFG);q._connect=self.connect;return q


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','NO')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','NO')
    monkeypatch.setattr('psycopg.connect',lambda *a,**k:pytest.fail('NO REAL SQL'))
    monkeypatch.setattr('requests.Session.send',lambda *a,**k:pytest.fail('NO EXTERNAL HTTP'))


@pytest.mark.parametrize('code', sorted(RUNTIME_SOURCES))
def test_all11_one_click_native_snapshot_is_signed_and_other_settings_preserved(code):
    minimum=20 if code=='intervesp' else 2
    db=DB(code);q=db.queue();request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,str(minimum),str(minimum+1),0)
    result=q.enqueue(1,trigger_type='manual',crawl_type='full',requested_by_user_id=1,
        source_mode='REFRESH',source_limit=5,manual_request=request)
    assert result.enqueued
    snap=db.snapshots[0];proof=verify(snap,KEY)
    assert proof['actor_id']==1 and proof['matching_apply'] is False
    assert proof['robots_attempts']==min(2,snap['rate_settings']['max_attempts'])
    assert snap['source_input']['canary']['selected']==5
    assert snap['rate_settings']['min_request_interval_seconds']==minimum
    assert snap['rate_settings']['jitter_max_seconds']==1
    assert MANUAL_POLICIES.get() is None
    assert not any('UPDATE suppliers SET rate_settings' in sql for sql,_ in db.sql)
    assert not any('UPDATE source_products' in sql for sql,_ in db.sql)


def test_intervesp_safe_pause_is_shown_and_validated_before_enqueue():
    initial={'delay_min_seconds':3,'delay_max_seconds':6}
    assert manual_collection.manual_launch_settings('intervesp',initial)=={
        'delay_min_seconds':20,'delay_max_seconds':20}
    db=DB('intervesp')
    approval=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'19','20',0)
    with pytest.raises(PermissionError,match='20 секунд'):
        db.queue().enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',
                           source_limit=5,manual_request=approval)
    assert not db.snapshots


@pytest.mark.parametrize('field',['rate','scope','signature','target','expired'])
def test_changed_or_expired_approval_refused(field):
    db=DB('uss_stanko');q=db.queue();r=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'2','3',0)
    q.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=2,manual_request=r)
    snap=deepcopy(db.snapshots[0])
    if field=='rate':snap['rate_settings']['max_concurrency']=8
    elif field=='scope':snap['source_input']['entries'][0]['url']='https://evil.invalid/'
    elif field=='signature':snap['manual_collection']['signature']='0'*64
    elif field=='target':snap['manual_collection']['target']={}
    with pytest.raises(PermissionError):verify(snap,KEY,now=datetime.now(timezone.utc)+timedelta(days=1) if field=='expired' else None)


def test_bad_speed_leaves_no_approval_context_or_job():
    db=DB('uss_stanko');q=db.queue();r=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'3','2',0)
    with pytest.raises(ValueError):q.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=2,manual_request=r)
    assert not db.snapshots and MANUAL_POLICIES.get() is None


def test_button_post_starts_exactly_one_worker_without_an_extra_preview(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch);calls=[];launches=[]
    def enqueue(*a,**k):calls.append(k);return EnqueueResult(44,'queued')
    c.app.state.queue.enqueue=enqueue
    monkeypatch.setattr('universal_supplier.control_plane.manual_collection.launch',lambda jid,**k:launches.append(jid))
    page=c.get('/suppliers/1');assert 'Начать парсинг' in page.text
    result=c.post('/suppliers/1/manual-start',data={'csrf_token':csrf(page),'revision':'0','max_products':'100','delay_min_seconds':'2','delay_max_seconds':'3'},follow_redirects=False)
    assert result.status_code==303 and '/runs?' in result.headers['location']
    assert launches==[44] and len(calls)==1 and calls[0]['source_limit']==100


def test_button_signs_with_baked_revision_without_git(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    db=DB('uss_stanko')
    c.app.state.queue.enqueue=db.queue().enqueue
    launched=[]
    monkeypatch.setattr(manual_collection,'launch',lambda jid,**kwargs:launched.append((jid,kwargs['key'])))
    monkeypatch.setattr(manual_collection,'ROOT',tmp_path)
    revision=tmp_path/'.release-revision'
    revision.write_text('a'*40+'\n',encoding='ascii')
    data={'csrf_token':csrf(c.get('/suppliers/1')),'revision':'0','max_products':'2',
          'delay_min_seconds':'2','delay_max_seconds':'3'}
    response=c.post('/suppliers/1/manual-start',data=data,follow_redirects=False)
    assert response.status_code==303 and 'job_id=44' in response.headers['location']
    assert len(launched)==len(db.snapshots)==1 and launched[0][0]==44
    assert verify(db.snapshots[0],launched[0][1])['head']=='a'*40
    revision.write_text('b'*40,encoding='ascii')
    with pytest.raises(PermissionError,match='Код изменился'):
        verify(db.snapshots[0],launched[0][1])


@pytest.mark.parametrize('revision',[None,'bad-revision'])
def test_no_worker_or_new_job_when_release_revision_unavailable(tmp_path,monkeypatch,revision):
    c,_=client(tmp_path,monkeypatch)
    db=DB('uss_stanko')
    c.app.state.queue.enqueue=db.queue().enqueue
    monkeypatch.setattr(manual_collection,'ROOT',tmp_path)
    monkeypatch.setattr(manual_collection,'launch',lambda *a,**k:pytest.fail('NO WORKER'))
    if revision is not None:
        (tmp_path/'.release-revision').write_text(revision,encoding='ascii')
    data={'csrf_token':csrf(c.get('/suppliers/1')),'revision':'0','max_products':'2',
          'delay_min_seconds':'2','delay_max_seconds':'3'}
    response=c.post('/suppliers/1/manual-start',data=data,follow_redirects=False)
    assert response.status_code==400 and 'Проверьте сборку панели' in response.text
    assert not db.snapshots


@pytest.mark.parametrize('case',['csrf','origin','viewer','readonly','bad_count'])
def test_manual_button_security_denies_before_enqueue(tmp_path,monkeypatch,case):
    c,store=client(tmp_path,monkeypatch,'VIEWER' if case=='viewer' else 'ADMIN');calls=[]
    c.app.state.queue.enqueue=lambda *a,**k:calls.append(k)
    token=csrf(c.get('/suppliers/1'))
    if case=='readonly':monkeypatch.setattr(type(store),'read_only',True)
    response=c.post('/suppliers/1/manual-start',data={'csrf_token':'bad' if case=='csrf' else token,'revision':'0',
        'max_products':'0' if case=='bad_count' else '100','delay_min_seconds':'2','delay_max_seconds':'3'},
        headers={'origin':'https://evil.invalid'} if case=='origin' else {},follow_redirects=False)
    assert response.status_code in (400,403) and not calls


def test_exact_claim_cannot_touch_foreign_jobs_or_run_with_changed_signature():
    db=DB('uss_stanko');q=db.queue();r=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'2','3',0)
    q.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=2,manual_request=r)
    snap=db.snapshots[0];seen=[];wrong=False
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args=None):self.sql=sql;seen.append((sql,args))
        def fetchone(self):
            if 'count(*)' in self.sql:return (0,)
            if 'SELECT j.id' in self.sql:return (45 if wrong else 44,1,'uss_stanko','uss_stanko','https://uss-stanko.com/','manual','full','queued',datetime.now(timezone.utc),snap)
            return None
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def transaction(self):return nullcontext()
        def cursor(self):return Cursor()
    q._connect=C
    job=q.claim_next(job_id=44,manual_validator=lambda s:verify(s,KEY))
    assert job.id==44
    assert any('WHERE j.id=%s' in sql and args==(44,) for sql,args in seen)
    assert not any('BATCH_WINDOW_EXPIRED' in sql for sql,_ in seen)
    seen.clear();wrong=True
    with pytest.raises(PermissionError):q.claim_next(job_id=44,manual_validator=lambda s:verify(s,KEY))
    assert not any("SET status='running'" in sql for sql,_ in seen)


@pytest.mark.parametrize(('running','background','signed_limit','claim'),[
    (11, 0, 12, True), (12, 0, 12, False), (3, 1, 12, False), (2, 0, None, False),
    (2, 0, 5, False),
])
def test_parallel_manual_claim_respects_capacity_and_signed_limit(running,background,signed_limit,claim):
    db = DB('uss_stanko')
    queue = db.queue()
    request = ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'2','3',0,
                            max_parallel_suppliers=signed_limit)
    queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',
                  source_limit=2,manual_request=request)
    snapshot = db.snapshots[0]
    statements = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def transaction(self): return nullcontext()
        def cursor(self): return self
        def execute(self, sql, args=None):
            self.sql = sql
            statements.append(sql)
        def fetchone(self):
            if 'count(*) FILTER' in self.sql:
                return running,background
            if 'SELECT count(*) FROM crawl_jobs' in self.sql:
                return running,
            if 'SELECT j.id' in self.sql:
                return (44,1,'uss_stanko','uss_stanko','https://uss-stanko.com/','manual','full',
                        'queued',datetime.now(timezone.utc),snapshot)
            return None

    queue._connect = Connection
    options = dict(job_id=44, manual_validator=lambda value:verify(value,KEY),
                   manual_max_parallel=12 if signed_limit is not None else None)
    if signed_limit == 5:
        with pytest.raises(PermissionError, match='Изменился подтверждённый лимит'):
            queue.claim_next(**options)
    else:
        assert (queue.claim_next(**options) is not None) is claim
    assert any("SET status='running'" in sql for sql in statements) is claim
    if claim:
        assert any('running.status=' in sql and 'FOR UPDATE OF j SKIP LOCKED' in sql for sql in statements)


def test_parallel_page_signs_a_separate_job_for_every_supported_supplier(tmp_path,monkeypatch):
    from universal_supplier.control_plane.source_jobs import RUNTIME_SOURCES

    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch)
    monkeypatch.setattr(store,'suppliers',lambda:[
        {'id':index,'code':code,'name':code,'products':index+100,'rate_settings':{},'crawl_settings':{}}
        for index,code in enumerate(RUNTIME_SOURCES,1)
    ])
    queue=c.app.state.queue
    requests=[];launches=[]
    def enqueue(sid,**kwargs):
        requests.append((sid,kwargs))
        return EnqueueResult(300+sid,'queued')
    monkeypatch.setattr(queue,'enqueue',enqueue)
    monkeypatch.setattr(manual_collection,'launch',lambda jid,**kwargs:launches.append(jid))
    endpoint='/suppliers/manual-parallel'
    page=c.get(endpoint)
    assert page.status_code==200 and 'Начать выбранные сборы' in page.text
    assert page.text.count('name="include_')==len(RUNTIME_SOURCES)
    form={'max_parallel_suppliers':str(len(RUNTIME_SOURCES)),'duration_hours':'4'}
    for sid in range(1,len(RUNTIME_SOURCES)+1):
        form.update({f'include_{sid}':'yes',f'limit_{sid}':str(10+sid),f'revision_{sid}':'0'})
    assert c.post(endpoint,data=form).status_code==403
    form['csrf_token']=csrf(page)
    assert c.post(endpoint,data={**form,'include_999':'yes'}).status_code==400
    assert c.post(endpoint,data={**form,'revision_12':'999'}).status_code==400
    assert not requests and not launches
    result=c.post(endpoint,data=form)
    assert result.status_code==200
    assert len(requests)==len(launches)==len(RUNTIME_SOURCES)
    assert requests[-1][1]['source_mode']=='DISCOVERY'
    for sid,options in requests:
        assert options['source_limit']==10+sid
        assert options['manual_request'].max_parallel_suppliers==len(RUNTIME_SOURCES)
        assert options['manual_request'].duration_hours==4
        assert f'/runs?job_id={300+sid}' in result.text
        assert result.text.count(f'data-collection-progress="/api/jobs/{300+sid}/progress"')==1
    requests.clear();launches.clear()
    search=c.post(endpoint,data={**form,'collection_mode':'DISCOVERY'})
    assert search.status_code==200 and len(launches)==len(RUNTIME_SOURCES)
    assert all(options['source_mode']=='DISCOVERY' for _,options in requests)


def test_parallel_form_requires_administrator(tmp_path,monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    c,_=client(tmp_path,monkeypatch,role='OPERATOR')
    assert c.get('/suppliers/manual-parallel').status_code==403
    assert c.post('/suppliers/manual-parallel/resume').status_code==403


def test_parallel_resume_uses_exact_selected_parents_and_isolates_refusals(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    monkeypatch.setattr(store,'suppliers',lambda:[
        {'id':1,'code':'kami','name':'КАМИ','products':7},
        {'id':2,'code':'optimum','name':'Optimum','products':9},
        {'id':3,'code':'ltt_msk','name':'LTT Москва','products':11},
    ])
    candidates=[
        dict(id=1,name='КАМИ',parent_job_id=21,run_id=31,remaining=2),
        dict(id=2,name='Optimum',parent_job_id=22,run_id=32,remaining=3),
        dict(id=3,name='LTT Москва',parent_job_id=23,run_id=33,remaining=4),
    ]
    queue=c.app.state.queue
    monkeypatch.setattr(queue,'manual_resume_candidates',lambda:candidates)
    requests=[]; launches=[]
    def enqueue(sid, **options):
        requests.append((sid, options))
        if sid==2:
            raise ValueError('RESUME_CHECKPOINT_CHANGED')
        return EnqueueResult(300+sid,'queued')
    monkeypatch.setattr(queue,'enqueue',enqueue)
    monkeypatch.setattr(manual_collection,'launch',lambda jid,**kwargs:launches.append(jid))
    page=c.get('/suppliers/manual-parallel')
    assert page.status_code==200
    assert 'Продолжить выбранные сборы' in page.text and 'Начать выбранные сборы' in page.text
    assert 'name="resume_1" value="21"' in page.text and '/runs/31' in page.text
    endpoint='/suppliers/manual-parallel/resume'
    form={'max_parallel_suppliers':'3','resume_2':'22','resume_1':'21'}
    assert c.post(endpoint,data=form).status_code==403
    form['csrf_token']=csrf(page)
    assert c.post(endpoint,data={**form,'resume_1':'20'}).status_code==400
    assert c.post(endpoint,data={**form,'max_parallel_suppliers':'13'}).status_code==400
    assert c.post(endpoint,data={**form,'resume_999':'99'}).status_code==400
    assert not requests and not launches
    result=c.post(endpoint,data={**form,'duration_hours':'24','max_products':'100000'})
    assert result.status_code==200
    assert {sid for sid,_ in requests}=={1,2} and launches==[301]
    for sid,options in requests:
        assert options['source_mode']=='RESUME' and options['source_limit'] is None
        assert options['resume_job_id']==20+sid
        assert options['manual_request'].max_parallel_suppliers==3
        assert options['manual_request'].duration_hours is None
    assert '/runs?job_id=301' in result.text and '/runs/32' in result.text
    assert 'Не удалось подтвердить целостность сохранённой очереди.' in result.text


def test_worker_accepts_signed_group_resume_with_original_limits():
    from types import SimpleNamespace
    from universal_supplier.control_plane.source_resume import digest as source_digest

    actor=AuthUser(1,'qa','QA','ADMIN')
    db=DB('uss_stanko')
    first=ManualRequest(actor,KEY,'2','3',0)
    db.queue().enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',
                       source_limit=2,manual_request=first)
    snapshot=deepcopy(db.snapshots[0])
    prior=snapshot.pop('manual_collection')
    original=snapshot['source_input']
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=44,
        resume_input_sha256=source_digest(original))
    snapshot['manual_resume']={'owner_job_id':44,'parent_job_id':44,'previous_engine':original['engine_sha256']}
    request=ManualRequest(actor,KEY,'','',-1,'RESUME',max_parallel_suppliers=3)
    request.validate(1,None,'manual','RESUME')
    request.nonce='resume-qa'
    request.policy=prior['policies']
    request.robots_attempts=prior['robots_attempts']
    cursor=SimpleNamespace(execute=lambda *_:None,fetchall=lambda:[(900,800)])
    snapshot['manual_collection']=request.seal(snapshot,cursor)
    assert verify(snapshot,KEY)['max_parallel_suppliers']==3
    assert snapshot['source_input']==db.snapshots[0]['source_input']
    assert snapshot['crawl_settings']['max_run_duration_seconds']==db.snapshots[0]['crawl_settings']['max_run_duration_seconds']
    request.retry_network=True
    with pytest.raises(PermissionError):
        request.validate(1,None,'manual','RESUME')


def test_worker_waits_for_exact_job_past_old_thirty_second_limit(monkeypatch):
    from types import SimpleNamespace
    from universal_supplier.control_plane import manual_worker, admin_store, orchestrator

    initial = datetime.now(timezone.utc)
    elapsed = 0
    proof = dict(actor_id=1, actor_role='ADMIN', actor_sv=1, nonce='qa', mappings=[],
                 policies={}, max_parallel_suppliers=12,
                 expires_at=(initial+timedelta(seconds=60)).isoformat())
    actor = AuthUser(1,'qa','QA','ADMIN',session_version=1)
    snapshot = {'synthetic':True}
    queue_calls = []
    executed = []

    class Clock:
        fromisoformat = staticmethod(datetime.fromisoformat)
        @staticmethod
        def now(tz):
            return initial + timedelta(seconds=elapsed)

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,args):
            self.sql = sql
            return self
        def fetchone(self):
            if 'settings_snapshot,status' in self.sql:return snapshot,'queued'
            if 'count(*)' in self.sql:return 1,
            assert self.sql == 'SELECT status FROM crawl_jobs WHERE id=%s'
            return 'queued',
        def fetchall(self): return []

    def claim_next(**options):
        queue_calls.append(options)
        if elapsed >= 36:
            options['manual_validator'](snapshot)
            return SimpleNamespace(id=44,settings_snapshot=snapshot)
        return None

    worker = SimpleNamespace(_run_job=lambda job:executed.append(job.id),
                             pool=SimpleNamespace(shutdown=lambda **kwargs:None))
    monkeypatch.setenv('SESSION_SIGNING_SECRET',KEY)
    monkeypatch.setattr(manual_worker,'os',SimpleNamespace(environ={'SESSION_SIGNING_SECRET':KEY}))
    monkeypatch.setattr(manual_worker,'time',SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(manual_worker,'datetime',Clock)
    monkeypatch.setattr(manual_worker,'verify',lambda snap,key:proof)
    monkeypatch.setattr(manual_worker,'runtime_config',lambda:None)
    monkeypatch.setattr(manual_worker,'guarded_connect',lambda *args,**kwargs:Connection())
    monkeypatch.setattr(manual_worker,'PostgresJobQueue',lambda *args,**kwargs:SimpleNamespace(claim_next=claim_next))
    monkeypatch.setattr(admin_store,'PostgresAdminStore',
                        lambda *args,**kwargs:SimpleNamespace(user_by_id=lambda actor_id:actor))
    monkeypatch.setattr(orchestrator,'Orchestrator',lambda *args,**kwargs:worker)
    manual_worker.run(44)
    assert elapsed==36 and executed==[44]
    assert all(c['job_id']==44 and c['manual_max_parallel']==12 for c in queue_calls)


def test_operator_cannot_change_admin_speed_and_disabled_source_requires_admin():
    db=DB('uss_stanko');q=db.queue();r=ManualRequest(AuthUser(2,'qa','QA','OPERATOR'),KEY,'2','3',0)
    with pytest.raises(PermissionError):q.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=2,manual_request=r)
    assert not db.snapshots and MANUAL_POLICIES.get() is None


def test_recovery_checks_scope_and_original_approval_before_dispatch():
    db = DB('uss_stanko')
    queue = db.queue()
    actor = AuthUser(1, 'qa', 'QA', 'ADMIN')
    other = AuthUser(2, 'other', 'Other', 'ADMIN')
    request = ManualRequest(actor, KEY, '2', '3', 0)
    queue.enqueue(1, trigger_type='manual', crawl_type='full', source_mode='REFRESH',
                  source_limit=2, requested_by_user_id=actor.id, manual_request=request)
    snapshot = db.snapshots[0]
    statements = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def transaction(self): return nullcontext()
        def cursor(self): return self
        def execute(self, sql, args=None):
            self.args = args
            statements.append(sql)
        def fetchone(self):
            if self.args == (44, 1):
                return snapshot, actor.id, True, 'https://uss-stanko.com/'

    queue._connect = Connection
    queue.recover_queued_manual_job(1, 44, actor, KEY, cancel=False)
    assert "j.status='queued'" in statements[-1] and 'FOR UPDATE OF j' in statements[-1]
    with pytest.raises(ValueError, match='принадлежит другому поставщику'):
        queue.recover_queued_manual_job(2, 44, actor, KEY, cancel=False)
    with pytest.raises(PermissionError, match='только его автор'):
        queue.recover_queued_manual_job(1, 44, other, KEY, cancel=False)
    snapshot['manual_collection']['expires_at'] = '2000-01-01T00:00:00+00:00'
    marker = snapshot['manual_collection']
    marker['signature'] = manual_collection.signature({k: v for k, v in marker.items() if k != 'signature'}, KEY)
    with pytest.raises(PermissionError, match='Время запуска истекло'):
        queue.recover_queued_manual_job(1, 44, actor, KEY, cancel=False)
    assert not any('UPDATE crawl_jobs' in sql for sql in statements)
    queue.recover_queued_manual_job(1, 44, other, KEY, cancel=True)
    assert "SET status='cancelled'" in statements[-1]
    queue.fail_waiting_manual_job(44, 'MANUAL_WORKER_REFUSED')
    assert "AND status='queued'" in statements[-1] and 'started_at IS NULL' in statements[-1]


def test_ivstankomaster_one_click_freezes_full_catalog_mode():
    db=DB('ivstankomaster')
    queue=db.queue()
    request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'2','3',0,'DISCOVERY')
    result=queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='DISCOVERY',
        source_limit=50,manual_request=request)
    assert result.enqueued
    snapshot=db.snapshots[0]
    assert snapshot['source_input']['mode']=='DISCOVERY'
    assert snapshot['source_input']['seeds']==['https://ivstankomaster.ru/category']
    assert snapshot['source_input']['entries']==[]
    assert snapshot['source_input']['discovery_known_urls']==[]
    assert snapshot['source_input']['canary']['max_products']==50
    assert verify(snapshot,KEY)['matching_apply'] is False


@pytest.mark.parametrize('code', RUNTIME_SOURCES)
def test_manual_new_search_signs_source_specific_catalog_scope(code):
    from universal_supplier.control_plane.source_discovery import root_urls
    db=DB(code)
    minimum=20 if code=='intervesp' else 2
    request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,str(minimum),str(minimum+1),0,'DISCOVERY')
    result=db.queue().enqueue(1,trigger_type='manual',crawl_type='full',source_mode='DISCOVERY',
                              source_limit=2,manual_request=request)
    assert result.enqueued
    snapshot=db.snapshots[0]
    value=snapshot['source_input']
    assert value['seeds']==root_urls(code) and value['discovery_known_urls']==[]
    assert value['entries']==[] and value['canary']['max_products']==2
    assert verify(snapshot,KEY)['matching_apply'] is False
    value['discovery_known_urls'].append('https://invalid.test/item/')
    with pytest.raises(PermissionError):
        verify(snapshot,KEY)
