"""UI→queue→snapshot→runtime, fake SQL/HTTP only, nine wrappers."""
from contextlib import nullcontext
from offline_host_profiles import approved_policy,approval_for
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from test_partner_pilot import CFG
from test_readiness_panel import PanelStore
from test_source_runtime import Backend
from test_source_jobs import saved_case
from test_host_fetch import Clock
from universal_supplier.control_plane.source_jobs import SOURCES,parse_existing
from universal_supplier.control_plane.source_canary import (limit_value,build_preview,validate_input,validate_connection,
    validate_live_selection,DispatchBudget,TARGET)
from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST,engine_fingerprint
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.control_plane.source_discovery import HOSTS
from universal_supplier.control_plane.queue import PostgresJobQueue,CrawlJob
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.rate_control import RequestAborted
from universal_supplier.http_capture import HttpResponse,sanitise_html

FIX=json.loads((Path(__file__).parent/'fixtures/beka_canary_five.json').read_text(encoding='utf-8'))

@pytest.fixture(autouse=True)
def offline_env(monkeypatch,tmp_path):
    import socket
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'run'))
    def deny(*a,**k):raise AssertionError('OFFLINE_NO_EXTERNAL_HTTP_OR_SQL')
    monkeypatch.setattr('psycopg.connect',deny)
    monkeypatch.setattr('requests.Session.send',deny)
    original=socket.socket.connect;resolver=socket.getaddrinfo
    def connect(sock,address):
        if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1') and address[1] not in (5432,55461,55452,55455,55449):return original(sock,address)
        return deny()
    def resolve(host,*a,**k):
        if host in ('localhost','127.0.0.1','::1',None):return resolver(host,*a,**k)
        return deny()
    monkeypatch.setattr(socket.socket,'connect',connect);monkeypatch.setattr(socket,'getaddrinfo',resolve)

def known_rows(code):
    if code=='beka_mak':
        rows=[(e['source_product_id'],e['source_external_id'],e['url'],109) for e in FIX['entries']]
        return rows+[(6000+i,str(80000+i),rows[0][2].rstrip('/')+f'-test-{i}/',109) for i in range(104)]
    _,url,_,_=saved_case(code)
    if code=='beka_mak_tr':return [(i+1,str(10000+i),url.replace('/bmso-460c/',f'/bmso-460c-test-{i}/'),109) for i in range(109)]
    return [(i+1,str(10000+i),url.rstrip('/')+f'-test-{i}/',109) for i in range(109)]

class FakeDB:
    def __init__(self,code='beka_mak',enabled=True):
        self.code=code;self.enabled=enabled;self.rows=known_rows(code);self.sql=[];self.snapshots=[]
        self.supplier_id=11 if code=='beka_mak' else 1
    @property
    def supplier(self):return (self.code,self.code,'https://'+HOSTS[self.code]+'/',self.enabled,{'host_policy_approval':approval_for('https://'+HOSTS[self.code])}, approved_policy(self.code,'https://'+HOSTS[self.code]).snapshot())
    def connect(self):
        db=self
        class Connection:
            read_only=False
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def transaction(self):return nullcontext()
            def cursor(self):return Cursor()
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,q,args=None):
                self.q=q;self.args=args;db.sql.append((q,args))
                if 'INSERT INTO crawl_jobs' in q:db.snapshots.append(json.loads(args[4]))
            def fetchone(self):
                if 'pg_control_system' in self.q:return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
                if 'FROM suppliers' in self.q:return db.supplier
                if 'SELECT id FROM crawl_jobs' in self.q:return None
                return (44,)
            def fetchall(self):
                assert 'WITH known AS' in self.q and 'ORDER BY id LIMIT %s' in self.q
                return db.rows[:self.args[1]]
        return Connection()
    def queue(self):
        q=PostgresJobQueue(CFG);q._connect=self.connect;return q

def store_for(db):
    class Store(PanelStore):
        def suppliers(self):
            row=super().suppliers()[0];row.update(id=db.supplier_id,enabled=db.enabled,base_url=db.supplier[2]);return [row]
    return Store(db.code)

def confirm(client,db,preview,**changes):
    form={'launch_mode':'source_canary','collection_mode':'REFRESH','max_products':'5',
          'source_preview_sha256':preview['confirmation_sha256']}
    form.update(changes)
    return client.post(f'/suppliers/{db.supplier_id}/start',data=form,headers={'origin':'http://testserver'},follow_redirects=False)

@pytest.mark.parametrize('code',SOURCES)
def test_all_nine_ui_queue_snapshot_runtime_five_from_109(monkeypatch,code):
    db=FakeDB(code);queue=db.queue();client=TestClient(create_app(store=store_for(db),queue=queue,auth_required=False))
    page=client.get(f'/suppliers/{db.supplier_id}/source-canary?max_products=5')
    assert page.status_code==200
    for text in ('109','REFRESH','max_products','55461','rc_verify_vi_20261019','300','20.0'):assert text in page.text
    preview=queue.preview_source_canary(db.supplier_id,5)
    assert confirm(client,db,preview).status_code==303
    assert len(db.snapshots)==1
    snapshot=db.snapshots[0];entries=snapshot['source_input']['entries']
    assert len(entries)==5 and snapshot['crawl_settings']['max_products']==5
    assert not snapshot['source_input']['seeds'] and snapshot['source_input']['mode']=='REFRESH'
    assert entries==[{'source_product_id':sid,'source_external_id':ext,'url':url} for sid,ext,url,total in db.rows[:5]]
    if code=='beka_mak':assert entries==FIX['entries']  # exact approved PK/ext/URL/order
    byurl={e['url']:e for e in entries};clock=Clock();calls=[];backend=Backend()
    body,url,charset,_=saved_case(code)
    model=parse_existing(code,sanitise_html(body,'text/html; charset='+charset).decode(),url)
    def parsed(source,text,target):
        e=byurl[target]
        if source=='kami':return replace(model,external_id=e['source_external_id'],requested_url=target,final_url=target,canonical_url=target)
        return replace(model,source_url=target,site_internal_id=e['source_external_id'])
    monkeypatch.setattr('universal_supplier.control_plane.source_runtime.parse_existing',parsed)
    monkeypatch.setattr('universal_supplier.control_plane.source_runtime.decode_node',lambda *a:pytest.fail('CANARY MUST NOT DISCOVER'))
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            validator(target);self.before_request(target);calls.append(target)
            if target.endswith('robots.txt'):return HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /')
            assert target in byurl
            return HttpResponse(target,200,'text/html',b'<h1>fake bounded response</h1>')
    job=CrawlJob(44,db.supplier_id,code,code,db.supplier[2],'manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
    finally:LEASED_HOST.reset(token)
    assert result['status']=='succeeded',result
    assert calls[1:]==list(byurl) and len(calls)==6  # robots1 + PRODUCT5, never product6
    assert len(backend.writes)==5 and len(backend.saved)==5 and backend.matches==[[e['source_external_id'] for e in entries]]
    assert result['canary']['product_requests']==list(byurl) and result['canary']['robots_requests']==1
    assert result['sql_committed']==5 and result['remaining']==0 and not result['deactivation_allowed']
    state=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert len(state['terminal'])==5 and not state['frontier'] and all(r['state']=='COMMITTED' for r in state['terminal'].values())
    original_calls=list(calls)
    resumed=replace(job,id=45,settings_snapshot={**snapshot,'crawl_settings':{**snapshot['crawl_settings'],
        'collection_mode':'RESUME','resume_owner_job_id':44,'resume_input_sha256':result['input_sha256']}})
    token=LEASED_HOST.set(job.domain)
    try:after=run_source(resumed,CFG,backend_factory=lambda *a:backend,client_factory=Client,clock=clock)
    finally:LEASED_HOST.reset(token)
    assert after['status']=='succeeded' and calls==original_calls and len(backend.writes)==5 and len(backend.matches)==1

@pytest.mark.parametrize('value',[None,True,False,0,-1,100001,5.0,'5'])
def test_invalid_limits_fail_closed(value):
    with pytest.raises(ValueError):limit_value(value)

def test_disabled_preview_visible_but_server_and_queue_never_enable_or_enqueue():
    db=FakeDB(enabled=False);queue=db.queue();client=TestClient(create_app(store=store_for(db),queue=queue,auth_required=False))
    page=client.get('/suppliers/11/source-canary?max_products=5')
    assert page.status_code==200 and 'disabled' in page.text and '109' in page.text
    preview=queue.preview_source_canary(11,5)
    assert confirm(client,db,preview).status_code==400
    with pytest.raises(ValueError,match='выключен'):queue.enqueue(11,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,source_preview_sha256=preview['confirmation_sha256'])
    assert db.enabled is False and not db.snapshots and all(not q.lstrip().startswith(('INSERT','UPDATE')) for q,args in db.sql)

@pytest.mark.parametrize('change',[
    {'max_products':'100'},{'max_products':'²'},{'max_products':''},
    {'collection_mode':'DISCOVERY'},{'collection_mode':'RESUME'},
    {'source_preview_sha256':'0'*64},
])
def test_server_tampering_cannot_fall_back_to_default100(change):
    db=FakeDB();queue=db.queue();client=TestClient(create_app(store=store_for(db),queue=queue,auth_required=False))
    preview=queue.preview_source_canary(11,5)
    assert confirm(client,db,preview,**change).status_code==400 and not db.snapshots

def test_changed_selection_between_preview_and_enqueue_requires_new_confirmation():
    db=FakeDB();queue=db.queue();preview=queue.preview_source_canary(11,5)
    sid,ext,url,total=db.rows[0];db.rows[0]=(sid,ext,url+'changed/',total)
    with pytest.raises(ValueError,match='заново'):queue.enqueue(11,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,source_preview_sha256=preview['confirmation_sha256'])
    assert not db.snapshots

@pytest.mark.parametrize('change',['entries109','limit100','modeDISCOVERY','seeds','database','rate','order'])
def test_snapshot_validation_rejects_widening(change):
    db=FakeDB();p=db.queue().preview_source_canary(11,5);s=deepcopy(p['snapshot']);v=s['source_input']
    if change=='entries109':v['entries']*=22
    if change=='limit100':s['crawl_settings']['max_products']=100
    if change=='modeDISCOVERY':v['mode']='DISCOVERY'
    if change=='seeds':v['seeds']=['https://beka-mak.su/sitemap.xml']
    if change=='database':v['canary']['database']['dbname']='sterbrust_unified_rc_20261019'
    if change=='rate':v['rate_policy']['max_concurrency']=2
    if change=='order':v['entries'].reverse()
    with pytest.raises((ValueError,PermissionError)):validate_input(v,s['crawl_settings'],CFG)

def test_dispatch_guard_refuses_sixth_product_and_duplicate():
    db=FakeDB();value=db.queue().preview_source_canary(11,5)['snapshot']['source_input']
    budget=DispatchBudget(value,'https://beka-mak.su/robots.txt');budget(budget.robots_url)
    for e in value['entries']:budget(e['url'])
    for url in (db.rows[5][2],db.rows[0][2],budget.robots_url):
        with pytest.raises(RequestAborted):budget(url)
    assert len(budget.products)==5 and budget.robots==1

def test_wrong_database_refused_before_any_connection():
    db=FakeDB();q=db.queue();q.config=replace(CFG,dbname='sterbrust_unified_rc_20261019')
    q._connect=lambda:pytest.fail('wrong DB must not connect')
    with pytest.raises(PermissionError):q.preview_source_canary(11,5)

def test_cluster_guard_refuses_wrong_system_before_start_or_write():
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self
        def execute(self,q):assert q.startswith('SELECT')
        def fetchone(self):return (TARGET['dbname'],'WRONG','17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
    with pytest.raises(PermissionError):validate_connection(C())

def test_slower_stored_host_interval_is_not_automatically_reduced():
    db=FakeDB();supplier=(*db.supplier[:5],approved_policy('beka_mak',db.supplier[2],{'min_request_interval_seconds':40}).snapshot())
    preview=build_preview(11,supplier,db.rows[:5],CFG,5)
    assert preview['snapshot']['rate_settings']['min_request_interval_seconds']==40

@pytest.mark.parametrize('change',['limit100','entries109','discover','missing_marker','wrong_db','scheduled'])
def test_runtime_rejects_tampered_canary_before_backend_or_http(change):
    db=FakeDB();snapshot=deepcopy(db.queue().preview_source_canary(11,5)['snapshot'])
    if change=='limit100':snapshot['crawl_settings']['max_products']=100
    if change=='entries109':snapshot['source_input']['entries']*=22
    if change=='discover':snapshot['crawl_settings']['collection_mode']='DISCOVERY'
    if change=='missing_marker':snapshot['source_input'].pop('canary')
    cfg=replace(CFG,dbname='sterbrust_unified_rc_20261019') if change=='wrong_db' else CFG
    job=CrawlJob(44,11,'beka_mak','beka_mak',db.supplier[2],'manual','full','running',None,snapshot)
    if change=='scheduled':job=replace(job,trigger_type='schedule')
    token=LEASED_HOST.set(job.domain)
    def forbidden(*a):pytest.fail('NO HTTP OR BACKEND START ALLOWED')
    try:
        with pytest.raises((ValueError,PermissionError)):
            run_source(job,cfg,backend_factory=forbidden,client_factory=forbidden,clock=Clock())
    finally:LEASED_HOST.reset(token)

def test_live_selection_guard_is_select_only_and_blocks_disabled():
    db=FakeDB();value=db.queue().preview_source_canary(11,5)['snapshot']['source_input'];queries=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,args=None):queries.append(q)
        def fetchone(self):
            if 'pg_control_system' in queries[-1]:return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
            return ('beka_mak',False)
    conn=SimpleNamespace(cursor=Cursor)
    with pytest.raises(PermissionError,match='выключен'):validate_live_selection(conn,value)
    assert len(queries)==2 and all(q.startswith('SELECT') for q in queries)

def test_sixth_product_is_blocked_at_real_fetcher_callback_not_after_response():
    from universal_supplier.control_plane.host_fetch import BoundedHostFetcher,compile_policy
    db=FakeDB();value=db.queue().preview_source_canary(11,5)['snapshot']['source_input']
    calls=[];budget=DispatchBudget(value,'https://beka-mak.su/robots.txt')
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            self.before_request(url);calls.append(url)
            return HttpResponse(url,200,'text/html',b'<h1>fake</h1>')
    fetch=BoundedHostFetcher('beka-mak.su',approved_policy('beka_mak','https://beka-mak.su'),Client,
        validator=lambda u:None,robots_allowed=lambda u:True,robots_delay_seconds=0,
        lease_acquired=True,source='beka_mak',approval=approval_for('https://beka-mak.su'),clock=Clock())
    fetch.request_start_guard=budget
    outcomes=list(fetch.fetch([r[2] for r in db.rows[:6]],limit=6,seconds=300))
    assert calls==[r[2] for r in db.rows[:5]] and len([r for r in outcomes if r.response])==5
    assert fetch.stop_reason=='CANARY_PRODUCT_REQUEST_LIMIT'

def test_redirect_outside_frozen_five_stops_next_products_and_preserves_first_stop():
    db=FakeDB();snapshot=db.queue().preview_source_canary(11,5)['snapshot'];backend=Backend();calls=[]
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            if url.endswith('robots.txt'):return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /')
            validator(db.rows[5][2])  # unapproved redirect: must not send or schedule next product
            pytest.fail('OUTSIDE REDIRECT MUST STOP')
    job=CrawlJob(44,11,'beka_mak','beka_mak',db.supplier[2],'manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=Client,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['termination_reason']=='CANARY_URL_OUTSIDE_FROZEN_BATCH' and len(calls)==2
    assert not backend.writes and not backend.matches
    stop=Path(result['checkpoint']).parent/'FIRST_STOP.json'
    assert json.loads(stop.read_text())['reason']=='CANARY_URL_OUTSIDE_FROZEN_BATCH'

def test_canary_resume_cannot_probe_another_database():
    from universal_supplier.control_plane.source_controls import verify_resume
    db=FakeDB();value=db.queue().preview_source_canary(11,5)['snapshot']['source_input']
    with pytest.raises(PermissionError,match='DATABASE'):verify_resume('beka_mak',44,'a'*64,value,replace(CFG,dbname='other'))

def test_resume_keeps_exact_identity_when_passive_ingest_made_committed_rows_inactive():
    db=FakeDB();value=db.queue().preview_source_canary(11,5)['snapshot']['source_input']
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,args=None):self.q=q;self.args=args
        def fetchone(self):
            if 'pg_control_system' in self.q:return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
            return ('beka_mak',True)
        def fetchall(self):
            assert 'AND (%s OR active)' in self.q
            assert self.args[:2]==(11,[e['source_product_id'] for e in value['entries']])
            return [r[:3] for r in db.rows[:5]] if self.args[2] else []
    conn=SimpleNamespace(cursor=Cursor)
    with pytest.raises(PermissionError,match='SELECTION_CHANGED'):validate_live_selection(conn,value)
    validate_live_selection(conn,value,allow_inactive=True)

@pytest.mark.parametrize('scenario',['csrf','origin','viewer'])
def test_new_mode_preserves_csrf_origin_and_roles(scenario):
    from test_security_review_group5 import _app,_login,_csrf
    app,_=_app();client=_login(app,'viewer1' if scenario=='viewer' else 'admin1')
    if scenario=='viewer':
        assert client.get('/suppliers/11/source-canary?max_products=5').status_code==403;return
    form={'launch_mode':'source_canary','collection_mode':'REFRESH','max_products':'5'}
    headers={}
    if scenario=='origin':form['csrf_token']=_csrf(client);headers['origin']='https://evil.example'
    assert client.post('/suppliers/11/start',data=form,headers=headers,follow_redirects=False).status_code==403
