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
    db=DB(code);q=db.queue();request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'2','3',0)
    result=q.enqueue(1,trigger_type='manual',crawl_type='full',requested_by_user_id=1,
        source_mode='REFRESH',source_limit=5,manual_request=request)
    assert result.enqueued
    snap=db.snapshots[0];proof=verify(snap,KEY)
    assert proof['actor_id']==1 and proof['matching_apply'] is False
    assert proof['robots_attempts']==min(2,snap['rate_settings']['max_attempts'])
    assert snap['source_input']['canary']['selected']==5
    assert snap['rate_settings']['min_request_interval_seconds']==2
    assert snap['rate_settings']['jitter_max_seconds']==1
    assert MANUAL_POLICIES.get() is None
    assert not any('UPDATE suppliers SET rate_settings' in sql for sql,_ in db.sql)
    assert not any('UPDATE source_products' in sql for sql,_ in db.sql)


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


def test_operator_cannot_change_admin_speed_and_disabled_source_requires_admin():
    db=DB('uss_stanko');q=db.queue();r=ManualRequest(AuthUser(2,'qa','QA','OPERATOR'),KEY,'2','3',0)
    with pytest.raises(PermissionError):q.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=2,manual_request=r)
    assert not db.snapshots and MANUAL_POLICIES.get() is None


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
    assert snapshot['source_input']['canary']['max_products']==50
    assert verify(snapshot,KEY)['matching_apply'] is False
