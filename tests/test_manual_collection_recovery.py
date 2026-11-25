"""The authenticated manual path uses the native queue, fetcher and receipts."""
from dataclasses import replace
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from urllib.error import URLError

import pytest

from offline_host_profiles import approval_for, approved_policy
from test_automation_admin import client, csrf
from test_host_fetch import Clock
from test_manual_collection import DB, KEY
from test_partner_pilot import html
from test_source_canary import CFG, build_preview
from test_source_jobs import deny
from test_source_runtime import Backend
from universal_supplier.control_plane.manual_collection import MANUAL_EXECUTION, ManualRequest, verify
from universal_supplier.control_plane.queue import CrawlJob, EnqueueResult
from universal_supplier.control_plane.security import AuthUser
from universal_supplier.control_plane.source_runtime import LEASED_HOST, engine_fingerprint, run_source
from universal_supplier.http_capture import HttpResponse


@pytest.fixture(autouse=True)
def offline(monkeypatch,tmp_path):
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    monkeypatch.setattr('psycopg.connect',deny)
    monkeypatch.setattr('requests.Session.send',deny)
    monkeypatch.setattr(socket,'getaddrinfo',deny)


def native_manual(*, fail_tail=False, end_window=False, block=False, robots_replies=None, robots_limit=2):
    code='partner_st'
    base='https://partner-st.ru'
    rate=approved_policy(code,base,{'max_attempts':2,'max_concurrency':1,'jitter_max_seconds':0})
    known=[{'url':base+f'/item-{i}/','source_external_id':str(i)} for i in (1,2,3)]
    data={'format':'source-runtime-input/v1','supplier_code':code,'mode':'REFRESH','entries':known,
          'seeds':[],'rate_policy':rate.snapshot(),'engine_sha256':engine_fingerprint()}
    settings={'collection_mode':'REFRESH','persist_source_data':True,'max_products':3,
              'max_run_duration_seconds':100,'host_policy_approval':approval_for(base)}
    job=CrawlJob(1,1,code,code,base+'/','manual','full','running',datetime.now(timezone.utc),
        {'source_input':data,'rate_settings':rate.snapshot(),'crawl_settings':settings,'manual_collection':{'signed':True}})
    if robots_replies is not None:
        supplier=(code,code,base+'/',True,{'host_policy_approval':approval_for(base),
                                          'max_run_duration_seconds':100},rate.snapshot())
        rows=[(i,entry['source_external_id'],entry['url'],len(known)) for i,entry in enumerate(known,1)]
        snapshot=build_preview(job.supplier_id,supplier,rows,CFG,len(rows))['snapshot']
        snapshot['manual_collection']={'robots_attempts':robots_limit}
        job=replace(job,settings_snapshot=snapshot)
    clock=Clock();backend=Backend()
    backend.matching=lambda ids:{'status':'succeeded','matching_applied':False}
    calls=[]
    persisted=backend.persist

    def persist(product,capture):
        written=persisted(product,capture)
        if end_window and len(backend.writes)==1:clock.sleep(100)
        return written

    backend.persist=persist

    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url)
            self.before_request(url)
            if url.endswith('/robots.txt'):
                if robots_replies is not None:
                    calls.append('robots')
                    status=next(robots_replies)
                    if status is None:raise URLError(TimeoutError('service timeout'))
                    if status!=200:return HttpResponse(url,status,'text/plain',b'unavailable')
                return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /\n')
            number=int(url.removesuffix('/').rsplit('-',1)[-1])
            calls.append(number)
            if number==1 and (calls.count(1)==1 or fail_tail):
                raise TimeoutError('body timeout')
            if number==2 and block:return HttpResponse(url,429,'text/plain',b'busy')
            return HttpResponse(url,200,'text/html; charset=utf-8',html(number).encode())

    def run(value):
        lease=LEASED_HOST.set(value.domain)
        auth=MANUAL_EXECUTION.set(value.id)
        try:return run_source(value,CFG if robots_replies is not None else None,
                              backend_factory=lambda cfg,source:backend,client_factory=Client,clock=clock)
        finally:
            MANUAL_EXECUTION.reset(auth)
            LEASED_HOST.reset(lease)
    return run,job,backend,calls


@pytest.mark.parametrize('fail_tail',[False,True])
def test_manual_timeout_moves_to_tail_once(fail_tail):
    run,job,backend,calls=native_manual(fail_tail=fail_tail)
    result=run(job)
    assert calls==[1,2,3,1]
    assert result['sql_committed']==(2 if fail_tail else 3)
    assert result['deferred']==int(fail_tail)
    assert result['status']==('partial' if fail_tail else 'succeeded')
    assert result['remaining']==0 and len(backend.writes)==result['sql_committed']
    assert any(summary.get('sql_committed')==1 and summary.get('deferred')==1 for summary in backend.summaries)


def test_manual_resume_skips_commit_and_retries_tail_in_saved_order():
    run,job,backend,calls=native_manual(end_window=True)
    first=run(job)
    assert calls==[1,2] and first['sql_committed']==1 and first['resume_available']
    assert first['termination_reason']=='MAX_RUN_DURATION_EXCEEDED'
    snapshot=job.settings_snapshot
    checkpoint=json.loads(Path(first['checkpoint']).read_bytes())
    resumed=replace(job,id=2,settings_snapshot={
        **snapshot,'manual_resume':{'previous_engine':snapshot['source_input']['engine_sha256'],
                                     'checkpoint_sha256':checkpoint['checksum']},
        'crawl_settings':{**snapshot['crawl_settings'],'collection_mode':'RESUME',
                          'resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    second=run(resumed)
    assert calls==[1,2,3,1]
    assert second['sql_committed']==3 and second['sql_committed_this_job']==2
    assert len(backend.writes)==3 and second['deferred']==0


def test_manual_rate_limit_still_stops_with_checkpoint():
    run,job,backend,calls=native_manual(block=True)
    result=run(job)
    assert calls==[1,2] and result['termination_reason']=='SOURCE_BLOCKED_HTTP_429'
    assert result['sql_committed']==0 and result['remaining']>=1


def test_manual_robots_timeout_retries_before_any_cards_with_frozen_budget():
    run,job,backend,calls=native_manual(robots_replies=iter((None,200)))
    result=run(job)
    assert calls==['robots','robots',1,2,3,1]
    assert result['status']=='succeeded' and result['sql_committed']==3
    assert result['canary']['robots_requests']==2
    phases=[summary['phase'] for summary in backend.summaries if 'phase' in summary]
    assert phases[:3]==['PREPARING','ROBOTS','PRODUCTS']


@pytest.mark.parametrize(('responses','limit','attempts','reason'),[
    ((None,None),2,2,'SOURCE_ROBOTS_UNAVAILABLE'),
    ((None,),1,1,'SOURCE_ROBOTS_UNAVAILABLE'),
    ((403,),2,1,'SOURCE_BLOCKED_HTTP_403'),
    ((429,),2,1,'SOURCE_BLOCKED_HTTP_429'),
])
def test_manual_robots_failures_stop_before_products(responses,limit,attempts,reason):
    run,job,backend,calls=native_manual(robots_replies=iter(responses),robots_limit=limit)
    result=run(job)
    assert calls==['robots']*attempts
    assert result['canary']['robots_requests']==attempts
    assert result['status']=='partial' and result['termination_reason']==reason
    assert result['sql_committed']==0 and result['remaining']==3 and not backend.writes
    if responses[0] is None:
        error=result['transport_errors'][0]['original_exception']
        assert error['type']=='URLError' and error['reason']['type']=='TimeoutError'


def test_night_budget_is_bound_to_one_job_and_non_admin_cannot_extend(monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    db=DB('kami');queue=db.queue()
    request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'3','5',0,duration_hours=15)
    queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,manual_request=request)
    snap=db.snapshots[0]
    assert snap['crawl_settings']['max_run_duration_seconds']==15*3600
    assert snap['source_input']['canary']['max_run_duration_seconds']==15*3600
    assert verify(snap,KEY)['matching_apply'] is False
    assert not any('UPDATE suppliers SET rate_settings' in sql for sql,_ in db.sql)
    operator=ManualRequest(AuthUser(2,'qa','QA','OPERATOR'),KEY,'3','5',0,duration_hours=15)
    with pytest.raises(PermissionError):
        queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,manual_request=operator)


def test_continue_request_launches_only_new_child(tmp_path,monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch);calls=[];launches=[]
    def enqueue(*args,**options):
        calls.append(options)
        return EnqueueResult(45,'queued')
    c.app.state.queue.enqueue=enqueue
    monkeypatch.setattr('universal_supplier.control_plane.manual_collection.launch',lambda jid,**k:launches.append(jid))
    token=csrf(c.get('/suppliers/1'))
    result=c.post('/suppliers/1/manual-start',data={'csrf_token':token,'collection_mode':'RESUME',
        'resume_job_id':'44'},follow_redirects=False)
    assert result.status_code==303 and calls[0]['resume_job_id']==44
    assert calls[0]['source_limit'] is None and launches==[45]
    assert 'job_id=45' in result.headers['location']


def test_job_progress_polls_and_api_omits_private_metadata(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    monkeypatch.setattr(type(store),'job_progress',lambda self,jid:{'job_status':'running','run_id':100,
        'total':10,'succeeded':2,'deferred':1,'private':'do not render'},raising=False)
    page=c.get('/runs?job_id=44&enqueue_status=queued')
    assert 'Задание №44' in page.text and '/api/jobs/44/progress' in page.text
    assert 'collection-progress.js' in page.text
    response=c.get('/api/jobs/44/progress')
    assert response.json()=={'job_status':'running','run_id':100,'total':10,'succeeded':2,'deferred':1}
    assert response.headers['cache-control']=='no-store'


def test_expired_job_action_is_scoped_and_requires_csrf(tmp_path,monkeypatch):
    from universal_supplier.control_plane.queue import InMemoryJobQueue
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch)
    supplier_rows=store.suppliers
    monkeypatch.setattr(store,'suppliers',lambda:[{**row,'manual_job_expired':True,
        'has_running_job':True,'job_id':176} for row in supplier_rows()])
    requests=[]
    monkeypatch.setattr(InMemoryJobQueue,'close_expired_manual_job',
        lambda self,supplier,job,actor_id:requests.append((supplier,job,actor_id)),raising=False)
    headers={'origin':'https://testserver'}
    endpoint='/suppliers/1/close-expired-job'
    assert c.post(endpoint,headers=headers,data={'job_id':'176'},follow_redirects=False).status_code==403
    page=c.get('/suppliers/1')
    assert 'Нет связи с исполнителем' in page.text and endpoint in page.text
    assert 'Начать парсинг' not in page.text and 'Сбор выполняется' not in page.text
    token=csrf(page)
    response=c.post(endpoint,headers=headers,data={'job_id':'176','csrf_token':token},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/suppliers/1'
    assert requests==[(1,176,1)]


@pytest.mark.parametrize(('expired','lock_acquired','run_owner','closes'),[
    (True,True,'176',True), (False,True,'176',False),
    (True,False,'176',False), (True,True,'177',False),
])
def test_close_expired_job_requires_expiry_released_lock_and_same_owner(expired,lock_acquired,run_owner,closes):
    statements=[]

    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def transaction(self):return nullcontext()
        def cursor(self):return self
        def execute(self,sql,args=None):
            self.sql=sql
            statements.append((sql,args))
        def fetchone(self):
            if 'pg_control_system' in self.sql:
                from universal_supplier.control_plane.source_canary import TARGET
                return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
            if 'FOR UPDATE OF j' in self.sql:
                return (67,None,None,'beka-mak.su','https://beka-mak.su/') if expired else None
            if 'pg_try_advisory_xact_lock' in self.sql:return (lock_acquired,)
            if 'FROM crawl_runs' in self.sql:return ('running',run_owner)
            raise AssertionError(self.sql)

    from universal_supplier.control_plane.queue import PostgresJobQueue
    queue=PostgresJobQueue(CFG)
    queue._connect=Connection
    if closes:
        queue.close_expired_manual_job(11,176,actor_id=1)
        assert [args[-1] for sql,args in statements if sql.startswith('UPDATE crawl_')]==[67,176]
        assert sum(sql.startswith('INSERT INTO audit_events') for sql,_ in statements)==1
    else:
        with pytest.raises((ValueError,PermissionError)):
            queue.close_expired_manual_job(11,176,actor_id=1)
        assert not any(sql.startswith(('UPDATE ', 'INSERT ')) for sql,_ in statements)
    assert any('j.id=%s AND j.supplier_id=%s' in sql and args==(176,11) for sql,args in statements)
