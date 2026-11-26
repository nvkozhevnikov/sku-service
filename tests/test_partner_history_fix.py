import json
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from universal_supplier.change_detection import (card_hashes,card_group_values,stable_hash,history_json,history_values_equal,commercial_values_equal)
from universal_supplier.models import ProductCard
from universal_supplier.postgres import PostgresRepository
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore

# Commercial fields from saved real pilot card 296; prose/media omitted.
def fixture_card():
    return ProductCard.from_jsonable(json.loads((Path(__file__).parent/'fixtures/partner_st_296_commercial.json').read_text(encoding='utf-8')))

@pytest.mark.parametrize('a,b,equal',[({'p':'232000'},{'p':Decimal('232000')},True),({'n':1},{'n':1.0},True),({'n':True},{'n':1},False),({'a':None},{},False),({'x':[1,2]},{'x':[2,1]},False),({'x':{'a':1,'b':2}},{'x':{'b':2,'a':1}},True)])
def test_history_jsonb_semantics(a,b,equal):assert history_values_equal(a,b)==equal

@pytest.mark.parametrize('field,value,equal',[('price','232000.00',True),('old_price',Decimal('257786.000'),True),('quantity','7.0',True),('price','232001',False),('availability_normalized','out_of_stock',False),('sku','0506001',False),('price',None,False)])
def test_commercial_actual_values(field,value,equal):
    old=json.loads(history_json(card_group_values(fixture_card())['commercial']));new=dict(old);new[field]=value
    assert commercial_values_equal(old,new)==equal

class Cursor:
    rowcount=1
    def __init__(self):self.calls=[]
    def execute(self,q,args):self.calls.append((q,args))
def test_equal_json_different_python_types_no_history():
    c=Cursor();assert PostgresRepository._record_history(c,1,1,None,3,'commercial',{'price':'232000'},{'price':Decimal('232000')},'now') is False
    assert c.calls==[]
def test_sql_guard_and_count_zero_when_insert_suppressed():
    c=Cursor();c.rowcount=0
    assert not PostgresRepository._record_history(c,1,1,None,3,'commercial',{'p':1},{'p':2},'now')
    assert 'WHERE %s::jsonb IS DISTINCT FROM %s::jsonb' in c.calls[0][0]

class IngestDB:
    rowcount=1
    def __init__(self,old,new,old_hash=None):
        self.old,self.new=old,new;self.old_hash=old_hash or card_hashes(old)['commercial'];self.calls=[]
    def transaction(self):return nullcontext()
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,q,args=None):
        if args is not None:assert q.count("%s")==len(args)
        self.q=q;self.calls.append((q,args))
    def fetchone(self):
        if 'identity_hash, description_hash' in self.q:
            h=card_hashes(self.old);meta={'_stage3b':{'options_hash':h['options'],'group_values':json.loads(history_json(card_group_values(self.old)))}}
            return (3,*[h[k] for k in ['identity','description','properties','images','documents','categories']],meta)
        if 'commercial_hash, raw_data,' in self.q:
            raw=json.loads(history_json(card_group_values(self.old)['commercial']))
            return (3,self.old_hash,{'_stage3b':{'commercial_value':raw}},raw)
        if 'SELECT crawl_settings' in self.q:return ({},'https://partner-st.ru/')
        return (3,)

def ingest(monkeypatch,old,new,old_hash=None):
    db=IngestDB(old,new,old_hash);repo=object.__new__(PostgresRepository);repo.connection=db
    monkeypatch.setattr(repo,'_record_quality_snapshot',lambda *a,**kw:SimpleNamespace(allow_missing_consequences=True,critical=False,codes=[],evidence={}))
    monkeypatch.setattr(repo,'_apply_missing',lambda *a,**kw:None)
    counts=repo.ingest([new],'2026-10-20T00:00:00Z',supplier_code='partner_st',crawl_run_id=7,bounded=True)
    return counts,db

def test_legacy_hash_backfills_without_false_change_or_timestamp(monkeypatch):
    card=fixture_card();legacy='4b6dc52ee0beff17502f30d0a6589ddb5cdd01281f3dc0b0f7f64f1847ed6126'
    assert legacy!=card_hashes(card)['commercial']
    counts,db=ingest(monkeypatch,card,card,legacy)
    assert counts.changed_offers==counts.change_history_events==0 and counts.unchanged_offers==1
    sql,args=next((q,a) for q,a in db.calls if 'INSERT INTO offers' in q)
    assert 'last_changed_at=CASE WHEN %s' in sql and args[-1] is False
    assert not any('INSERT INTO entity_change_history' in q for q,a in db.calls)
    source_sql=next(q for q,a in db.calls if 'INSERT INTO source_products' in q)
    assert 'catalog_product_id=' not in source_sql and 'product_matches' not in ' '.join(q for q,a in db.calls)
    again,_=ingest(monkeypatch,card,card)
    assert again.changed_offers==again.change_history_events==0

@pytest.mark.parametrize('change',[{'price':Decimal('232001')},{'availability_normalized':'out_of_stock','availability_raw':'https://schema.org/OutOfStock'}])
def test_true_price_or_availability_records_one_then_repeat_zero(monkeypatch,change):
    old=fixture_card();new=replace(old,**change)
    first,db=ingest(monkeypatch,old,new)
    assert first.changed_offers==first.change_history_events==1
    sql,args=next((q,a) for q,a in db.calls if 'INSERT INTO offers' in q);assert args[-1] is True
    again,_=ingest(monkeypatch,new,new);assert again.changed_offers==again.change_history_events==0

@pytest.mark.parametrize('message',['CheckViolation: entity_change_history_actual_change_ck','<script>bad()</script>',None])
def test_failed_ingest_ui_no_false_no_errors(message):
    class Store(UnavailableControlPlaneStore):
        def run(self,id):return {'id':id,'supplier_name':'Partner-ST','status':'failed','started_at':'2026-10-20','finished_at':'2026-10-20','error_count':0,'errors':[],'metadata':{'failure':message,'http_phase':{'successful_cards':50,'selected_urls':50,'errors':0,'duration_seconds':105.6,'cards_per_minute':28.4}}}
    client=TestClient(create_app(store=Store('offline'),auth_required=False));r=client.get('/runs/6')
    assert r.status_code==200 and 'Ошибок нет.' not in r.text and 'Обновление данных не завершено' in r.text and 'HTTP-этап' in r.text
    if message=='<script>bad()</script>':assert message not in r.text and '&lt;script&gt;' in r.text

def test_http_receipt_survives_ingest_failure(tmp_path,monkeypatch):
    import test_partner_pilot as pilot
    from universal_supplier.control_plane import adapter_registry as ar
    from universal_supplier.control_plane.queue import CrawlJob
    from unittest.mock import MagicMock
    result,_=pilot.run(tmp_path,3);events=[];repo=MagicMock();repo.connection.transaction.side_effect=lambda:nullcontext()
    def fail(*a,**kw):events.append('ingest');raise RuntimeError('SQL failure')
    repo.ingest.side_effect=fail
    monkeypatch.setattr(ar,'PostgresRepository',lambda c:repo)
    monkeypatch.setattr(ar,'_run_with_progress',lambda *a,**kw:result)
    monkeypatch.setattr(ar,'_finish_metadata',lambda r,i,p:events.append(('metadata',p)))
    matching=[];monkeypatch.setattr(ar,'run_post_ingest',lambda *a:matching.append(a))
    for k,v in {'STERBRUST_PARTNER_PILOT':'YES','STERBRUST_ALLOW_SUPPLIER_HTTP':'YES','PARTNER_PILOT_EVIDENCE_ROOT':str(tmp_path),'PARTNER_PILOT_STOP_FILE':str(tmp_path/'stop')}.items():monkeypatch.setenv(k,v)
    job=CrawlJob(8,1,'partner_st','partner_st','https://partner-st.ru/','manual','full','running',None,{'partner_pilot':pilot.batch(3),'rate_settings':{'max_concurrency':1,'min_request_interval_seconds':2,'max_attempts':1,'request_timeout_seconds':20.0,'max_run_duration_seconds':300}})
    with pytest.raises(RuntimeError,match='SQL failure'):ar.run_partner_st_job(job,pilot.CFG)
    assert events[0][0]=='metadata' and events[0][1]['http_phase']['successful_cards']==3 and events[1]=='ingest'
    saved=json.loads((tmp_path/'8/HTTP_PHASE.json').read_text(encoding='utf-8'));assert saved['successful_cards']==len(saved['captures'])==3
    assert not matching and repo.record_crawl_error.call_args.kwargs['error_type']=='INGEST_OR_PIPELINE_FAILED'


def test_stale_snapshot_does_not_hide_actual_offer_change(monkeypatch):
    observed=fixture_card();actual=replace(observed,availability_normalized='unknown')
    original=IngestDB.fetchone
    def fetch(db):
        row=original(db)
        if 'commercial_hash, raw_data,' in db.q:
            stale=json.loads(history_json(card_group_values(observed)['commercial']))
            return (row[0],row[1],{'_stage3b':{'commercial_value':stale}},row[3])
        return row
    monkeypatch.setattr(IngestDB,'fetchone',fetch)
    counts,db=ingest(monkeypatch,actual,observed)
    assert counts.changed_offers==counts.change_history_events==1
    args=next(a for q,a in db.calls if 'INSERT INTO entity_change_history' in q)
    assert 'unknown' in args[5] and 'in_stock' in args[6]

@pytest.mark.parametrize('name,workers,interval',[('conservative',1,2.0),('compare_2',2,1.0),('compare_4',4,.5),('compare_4_fast',4,.2)])
def test_profile_snapshot_roundtrip(name,workers,interval):
    from universal_supplier.partner_pilot import snapshot,validate_snapshot,rate_profile
    p=snapshot(1,[(3,'296','https://partner-st.ru/product.html')],name)
    assert validate_snapshot(json.loads(json.dumps(p)))==p['batch']
    assert rate_profile(p['rate_profile'])['max_concurrency']==workers
    assert rate_profile(name)['min_request_interval_seconds']==interval
    p['rate_profile']='unsafe'
    with pytest.raises(ValueError,match='UNKNOWN_PILOT_RATE_PROFILE'):validate_snapshot(p)

def test_stage_timings_are_recorded_on_saved_runner(tmp_path):
    import test_partner_pilot as pilot
    result,_=pilot.run(tmp_path,3)
    assert len(result.rows)==3
    assert all(r['http_seconds']>=0 and r['parsing_seconds']>=0 for r in result.rows)


@pytest.mark.parametrize('profile,status',[('conservative',303),('compare_2',303),('compare_4',303),('compare_4_fast',303),('arbitrary',400)])
def test_ui_rate_profiles_validated_and_forwarded(monkeypatch,profile,status):
    class Queue:
        def __init__(self):self.calls=[]
        def enqueue(self,*a,**kw):self.calls.append(kw);return SimpleNamespace(status='queued',job_id=1,deduplicated_job_id=None)
    monkeypatch.delenv('STERBRUST_PARTNER_PILOT',raising=False)
    q=Queue();client=TestClient(create_app(store=UnavailableControlPlaneStore('offline'),queue=q,auth_required=False))
    r=client.post('/suppliers/1/start',data={'launch_mode':'test','max_products':'50','pilot_profile':profile},headers={'origin':'http://testserver'},follow_redirects=False)
    assert r.status_code==status
    if status==303:assert q.calls[-1]['pilot_profile']==profile and q.calls[-1]['pilot_limit']==50
    else:assert not q.calls
