from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from contextlib import nullcontext
import json
import pytest
from fastapi.testclient import TestClient
from universal_supplier.partner_pilot import snapshot,validate_snapshot,limit_value,validate_config,connect_postgres
from universal_supplier.postgres import PostgresConfig
from universal_supplier.crawl import run_partner_st_live
from universal_supplier.models import FetchRecord
from universal_supplier.control_plane.rate_control import DomainRatePolicy
from universal_supplier.control_plane.queue import PostgresJobQueue,CrawlJob
from universal_supplier.control_plane import adapter_registry as ar
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore

CFG=PostgresConfig('127.0.0.1',55461,'rc_verify_vi_20261019','rc_owner',None,'disable')
def batch(n=50):return snapshot(n,[(i,str(i),f'https://partner-st.ru/item-{i}/') for i in range(1,n+1)])
def html(i):return f'<h1>Станок BD-{i}</h1><input name="product_data[{i}][product_id]" value="{i}"><script type="application/ld+json">'+json.dumps({'@type':'Product','name':f'Станок BD-{i}','sku':f'BD-{i}','offers':{'price':'12345','priceCurrency':'RUB','availability':'https://schema.org/InStock'}})+'</script>'
class Transport:
    def __init__(self,**kw):self.calls=[];self.policy=kw['domain_policy']
    def get(self,url):raise AssertionError('discovery forbidden')
    def get_validated(self,url,validator):
        validator(url);self.calls.append(url);i=url.split('item-')[1].strip('/')
        return FetchRecord(url,url,200,(),1,2,html(i))

def run(tmp_path,n=50,**kw):
    transports=[]
    def make(**kwargs):t=Transport(**kwargs);transports.append(t);return t
    r=run_partner_st_live(baseline=1225,rate_policy=DomainRatePolicy(max_concurrency=1),pilot=batch(n),evidence_root=tmp_path,transport_factory=make,**kw)
    return r,transports[0]

@pytest.mark.parametrize('value',[0,-1,51,True,False,'50',None,1.2])
def test_invalid_limits_fail_closed(value):
    with pytest.raises(ValueError):limit_value(value)

def test_actual_runner_caps_unique_urls_no_discovery_and_repeat(tmp_path):
    for _ in range(2):
        r,t=run(tmp_path)
        assert len(set(t.calls))==len(t.calls)==50
        assert len(r.cards)==len(r.captures)==50
        assert not r.health.deactivation_allowed
        assert r.discovery_metadata['scope']=='SAVED_URL_LIVE_NO_DISCOVERY'
        assert r.cards[0].price==Decimal('12345')
        assert all(__import__('pathlib').Path(x['evidence_ref']).is_file() for x in r.captures)

def test_stop_schedules_zero_http(tmp_path):
    r,t=run(tmp_path,stop_requested=lambda:True)
    assert not t.calls and not r.cards and r.termination_reason=='STOP_REQUESTED'

@pytest.mark.parametrize('field,value',[('limit',51),('batch_sha256','x'),('mode','FULL'),('database',['127.0.0.1',55461,'sterbrust_unified_rc_20261019']),('system_identifier','other')])
def test_tampered_job_snapshot(field,value):
    p=batch(3);p[field]=value
    with pytest.raises((ValueError,PermissionError)):validate_snapshot(p)

def test_duplicate_urls_and_ids_rejected():
    from universal_supplier.partner_pilot import digest
    for key in ('url','external_id','source_product_id'):
        p=batch(2);p['batch'][1][key]=p['batch'][0][key];p['batch_sha256']=digest(p['batch'])
        with pytest.raises(ValueError):validate_snapshot(p)

@pytest.mark.parametrize('field,value',[('dbname','sterbrust_unified_rc_20261019'),('port',55452),('host','localhost')])
def test_wrong_db_refused_before_connection(monkeypatch,field,value):
    monkeypatch.setenv('STERBRUST_PARTNER_PILOT','YES')
    monkeypatch.setattr('psycopg.connect',lambda **kw:pytest.fail('must not connect'))
    with pytest.raises(PermissionError):connect_postgres(replace(CFG,**{field:value}),autocommit=True)

def test_wrong_cluster_stays_readonly_and_closes(monkeypatch):
    monkeypatch.setenv('STERBRUST_PARTNER_PILOT','YES')
    class C:
        def __init__(self):self.closed=False;self.sql=[]
        def cursor(self,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q):self.sql.append(q)
        def fetchone(self):return (CFG.dbname,'wrong','17.11','wrong','127.0.0.1',55461)
        def close(self):self.closed=True
    c=C();args={}
    def connect(**kw):args.update(kw);return c
    monkeypatch.setattr('psycopg.connect',connect)
    with pytest.raises(PermissionError):connect_postgres(CFG,autocommit=True)
    assert c.closed and args['options']=='-c default_transaction_read_only=on'
    assert len(c.sql)==1 and c.sql[0].startswith('SELECT')

def test_worker_scopes_ingest_and_matching(tmp_path,monkeypatch):
    r,_=run(tmp_path,3)
    from unittest.mock import MagicMock
    repo=MagicMock();repo.connection.transaction.side_effect=lambda:nullcontext()
    repo.ingest.return_value=SimpleNamespace(new_products=0);repo.final_crawl_run_status.return_value='partial'
    monkeypatch.setattr(ar,'PostgresRepository',lambda cfg:repo)
    monkeypatch.setattr(ar,'_run_with_progress',lambda *a,**kw:r)
    called=[];monkeypatch.setattr(ar,'run_post_ingest',lambda cfg,code,ids:called.append((code,ids)) or {'status':'succeeded'})
    for key,value in {'STERBRUST_PARTNER_PILOT':'YES','STERBRUST_ALLOW_SUPPLIER_HTTP':'YES','PARTNER_PILOT_EVIDENCE_ROOT':str(tmp_path),'PARTNER_PILOT_STOP_FILE':str(tmp_path/'stop')}.items():monkeypatch.setenv(key,value)
    job=CrawlJob(1,1,'partner_st','partner_st','https://partner-st.ru/','manual','full','running',None,{'partner_pilot':batch(3),'rate_settings':{'max_concurrency':1,'min_request_interval_seconds':2,'max_attempts':1,'max_run_duration_seconds':300}})
    ar.run_partner_st_job(job,CFG)
    assert called==[('partner_st',['1','2','3'])]
    assert repo.ingest.call_args.kwargs['bounded'] is True
    assert repo.ingest.call_args.kwargs['discovery_health'].deactivation_allowed is False
    repo.record_partner_pilot_captures.assert_called_once()
    assert len(repo.ingest.call_args.args[0])==3

def test_queue_snapshot_persists_validated_batch_and_frozen_settings(monkeypatch):
    monkeypatch.setenv('STERBRUST_PARTNER_PILOT','YES')
    crawl={'baseline_discovered_count':1225};rate={'max_concurrency':4};captured={}
    class Q:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def transaction(self):return nullcontext()
        def cursor(self):return self
        def execute(self,q,args=None):
            self.q=q
            if 'INSERT INTO crawl_jobs' in q:captured.update(json.loads(args[4]))
        def fetchone(self):
            if 'SELECT code' in self.q:return ('partner_st','partner_st','https://partner-st.ru/',True,crawl,rate)
            if 'SELECT id FROM crawl_jobs' in self.q:return None
            return (44,)
        def fetchall(self):return [(1,'1','https://partner-st.ru/item-1/')]
    q=PostgresJobQueue(CFG);monkeypatch.setattr(q,'_connect',Q)
    result=q.enqueue(1,trigger_type='manual',crawl_type='full',pilot_limit=1)
    crawl['baseline_discovered_count']=0;rate['max_concurrency']=64
    assert result.job_id==44 and captured['crawl_settings']['baseline_discovered_count']==1225
    assert captured['partner_pilot']['limit']==1 and len(validate_snapshot(captured['partner_pilot']))==1
    assert captured['rate_settings']['max_concurrency']==1
    with pytest.raises(PermissionError):q.enqueue(1,trigger_type='manual',crawl_type='full')

@pytest.mark.parametrize('mode,limit,status',[('test','50',303),('test','51',400),('test','0',400),('test','2.5',400),('bad','1',400),('full','',303)])
def test_web_validates_mode_and_limit(monkeypatch,mode,limit,status):
    monkeypatch.delenv('STERBRUST_PARTNER_PILOT',raising=False)
    class Queue:
        calls=[]
        def enqueue(self,*a,**kw):self.calls.append(kw);return SimpleNamespace(status='queued',job_id=1,deduplicated_job_id=None)
    q=Queue();client=TestClient(create_app(store=UnavailableControlPlaneStore('offline'),queue=q,auth_required=False))
    response=client.post('/suppliers/1/start',data={'launch_mode':mode,'max_products':limit},headers={'origin':'http://testserver'},follow_redirects=False)
    assert response.status_code==status
    if status==303:assert q.calls[-1].get('pilot_limit')==(50 if mode=='test' else None)

def test_bounded_ingest_skips_all_global_reconciliation_and_missing(monkeypatch):
    from universal_supplier.postgres import PostgresRepository
    repo=object.__new__(PostgresRepository)
    class C:
        def transaction(self):return nullcontext()
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,args=None):self.q=q
        def fetchone(self):return ({},'https://partner-st.ru/') if 'crawl_settings' in self.q else (1,)
    repo.connection=C()
    for method in ['ensure_supplier','_bootstrap_canonical_properties','_reconcile_supplier_properties','_reconcile_sterbrust_property_evidence','_upsert_navigation_relations']:
        monkeypatch.setattr(repo,method,lambda *a:pytest.fail('global reconciliation forbidden'))
    monkeypatch.setattr(repo,'_record_quality_snapshot',lambda *a,**kw:SimpleNamespace(allow_missing_consequences=True,critical=False,codes=[],evidence={}))
    calls=[];monkeypatch.setattr(repo,'_apply_missing',lambda *a,**kw:calls.append(a[5]))
    from universal_supplier.state import discovery_health
    repo.ingest([], '2026-10-20T00:00:00Z',supplier_code='partner_st',crawl_run_id=1,discovery_health=discovery_health(1225,1225),bounded=True)
    assert calls==[False]

def test_correct_cluster_guard_checks_host_address_before_enabling_writes(monkeypatch):
    from universal_supplier.partner_pilot import PGDATA,SYSTEM
    monkeypatch.setenv('STERBRUST_PARTNER_PILOT','YES')
    class C:
        sql=[]
        def cursor(self,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q):self.sql.append(q)
        def fetchone(self):return (CFG.dbname,SYSTEM,'17.11',str(PGDATA),'127.0.0.1',55461)
        def close(self):pytest.fail('valid identity should remain connected')
    c=C();monkeypatch.setattr('psycopg.connect',lambda **kw:c)
    assert connect_postgres(CFG,autocommit=True) is c
    assert 'host(inet_server_addr())' in c.sql[0]
    assert c.sql[1]=='SET default_transaction_read_only=off'
