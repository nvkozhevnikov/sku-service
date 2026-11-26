"""Commercial provenance and exact confirmed-refresh guards. NO HTTP or testDB writes."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from universal_supplier.commercial_persistence import native_observation,normalize_availability
from universal_supplier.price_semantics import price_type_proof
from universal_supplier.control_plane import commercial_refresh as cr,automation_execution as ex
from universal_supplier.control_plane.source_jobs import frozen_identity
from universal_supplier.source_update import commercial_projection

@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def deny(*a,**k):raise AssertionError('OFFLINE_NO_HTTP_OR_SQL')
    monkeypatch.setattr('psycopg.connect',deny)
    monkeypatch.setattr('requests.Session.send',deny)

@pytest.mark.parametrize('label,expected',[('100000 ₽','unknown'),('Цена','unknown'),
    ('Розничная цена 100 ₽','retail'),('Рекомендованная розничная цена 100 ₽','RRP'),
    ('Оптовая цена','wholesale'),('Дилерская цена','dealer'),('Акционная цена','promo'),
    ('Розничная цена / Оптовая цена','unknown')])
def test_price_type_requires_explicit_unambiguous_price_witness(label,expected):
    assert price_type_proof(label)['classification']==expected

@pytest.mark.parametrize('raw,expected',[('Discontinued','discontinued'),('https://schema.org/OutOfStock','out_of_stock'),
    ('BackOrder','backorder'),('PreOrder','preorder'),('https://other.test/InStock','unknown'),('https://schema.org/InStock?x=1','unknown')])
def test_negative_stock_and_schema_uri_are_not_lost(raw,expected):assert normalize_availability(raw)==expected

def native():
    from test_partner_pilot import html
    from universal_supplier.control_plane.source_jobs import parse_existing
    from universal_supplier.commercial_persistence import HttpCapture
    u='https://partner-st.ru/item-1/'
    card=parse_existing('partner_st',html(1),u)
    cap=HttpCapture(u,u,200,'text/html',datetime(2026,10,27,tzinfo=timezone.utc),'a'*64,'b'*64,'fixture://commercial')
    return card,cap

@pytest.mark.parametrize('price,currency,raw,state',[(Decimal('12.30'),'RUB','12,30','numeric_public'),
    (None,'','Цена по запросу','price_on_request'),(Decimal(0),'RUB','0','zero_invalid'),
    (Decimal('12'),'RUBLE','12','parse_error'),(None,'','','missing')])
def test_native_observation_retains_provenance_and_fails_closed(price,currency,raw,state):
    card,cap=native();card=replace(card,price=price,currency=currency,price_raw=raw,price_source='own block')
    first=native_observation(card,cap);second=native_observation(card,cap)
    assert first==second and first.price_raw==raw and first.price_source=='own block'
    assert first.price_state.value==state and first.price_type=='unknown'
    assert (first.price is not None)==(state=='numeric_public')

def test_beka_attribute_order_and_negative_stock_scope():
    from universal_supplier.adapters.bekamak import parse_bekamak_detail
    html='''<h1 id="pagetitle">BMS-230DG</h1><script>setViewedProduct(19240,1)</script>
      <div class="prices_block"><span data-value="772191" data-price-type="retail" data-currency="RUB" class="price extra">Розничная цена</span></div>
      <link content="https://schema.org/OutOfStock" itemprop="availability">
      Похожее оборудование <meta itemprop="availability" content="https://schema.org/InStock">'''
    p=parse_bekamak_detail(html,source_url='https://beka-mak.su/product/bms_230dg/')
    assert p.price.current_price==Decimal('772191') and p.availability=='OutOfStock'
    assert p.price.price_type=='retail'

def test_intervesp_does_not_invent_rubles_or_borrow_recommendation_stock():
    from universal_supplier.adapters.intervesp import parse_intervesp_detail
    html='''<h1>Beka Mak BMS-230DG</h1><div id="bx_abc_8992" class="el_Main extra">
      <div id="elPrice"><meta content="1000" itemprop="price"></div>
      <link itemprop="availability" href="https://schema.org/BackOrder"></div>
      <meta itemprop="availability" content="https://schema.org/InStock">'''
    u='https://intervesp.ru/catalog/bandsaw/bms-230dg/'
    p=parse_intervesp_detail(html,source_url=u)
    assert p.site_internal_id=='8992' and p.availability=='BackOrder' and p.price.current_price is None
    p=parse_intervesp_detail(html.replace('<meta content="1000"','<meta itemprop="priceCurrency" content="RUB"><meta content="1000"'),source_url=u)
    assert p.price.current_price==Decimal('1000')

def test_intervesp_existing_listing_identity_is_reused_only_for_same_url():
    from universal_supplier.adapters.intervesp import parse_intervesp_detail
    from universal_supplier.intervesp_listing import url_identity
    u='https://intervesp.ru/catalog/bandsaw/bms-230dg/'
    p=parse_intervesp_detail('<h1>BMS-230DG</h1><div class="el_Main" id="bx_a_8992"></div>',source_url=u)
    entry={'url':u,'source_external_id':url_identity(u)}
    assert frozen_identity('intervesp',p,entry).supplier_external_id==entry['source_external_id']
    assert frozen_identity('intervesp',replace(p,source_url=u+'other/'),entry).supplier_external_id is None
    assert frozen_identity('intervesp',replace(p,site_internal_id=None),entry).supplier_external_id is None


def test_optimum_currency_and_stock_are_scoped_to_own_product():
    from test_optimum_source import HTML,URL
    from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
    html=HTML.replace('<meta itemprop="priceCurrency" content="RUB">','').replace('200 000 ₽','200 000')
    html=html.replace('<div class="catalog-element-quantity"><span class="catalog-element-quantity-value-text">В наличии 5</span></div>','')
    related='<div itemtype="https://schema.org/Product"><meta itemprop="priceCurrency" content="USD"><div class="catalog-element-quantity">В наличии 99</div></div>'
    p=OptimumProductParser().parse_product(related+html,URL)
    assert Decimal(p.commercial.price)==Decimal('191487') and p.commercial.currency is None
    from universal_supplier.adapters.optimum import to_product_card
    assert p.commercial.availability_normalized is None
    assert to_product_card(p,final_url=URL).availability_normalized=='unknown'


def test_price_type_for_preview_uses_current_proof_not_old_offer_classification():
    from universal_supplier.price_semantics import observation_price_type
    row={'observed_price_type':'unknown','price_type':'dealer',
        'extraction_evidence':{'business_price_type':price_type_proof('РРЦ',source_path='own price block')}}
    assert observation_price_type(row)=='RRP'
    row['extraction_evidence']={}
    assert observation_price_type(row)=='unknown'

def test_commercial_projection_preserves_all_identity_content_and_history_basis():
    old,cap=native();new=replace(old,price=Decimal('99'),name='Other name',sku='Other SKU',description_text='replacement',properties=(),media=())
    safe=commercial_projection(old,new)
    assert safe.price==Decimal('99')
    for key in ('external_id','name','sku','properties','media','options','categories','brand','description_text'):
        assert getattr(safe,key)==getattr(old,key)

@pytest.mark.parametrize('flag,value',[('price_basis','from_price'),('variant_or_group',True),('commercial_only',True)])
def test_non_exact_price_and_commercial_only_content_do_not_become_fresh(flag,value):
    from test_automation_system import snapshot,NOW
    from universal_supplier.commercial_freshness import assess
    row=snapshot()['records'][0];row['extraction_evidence']={flag:value}
    result=assess(row,{'price_ttl':21600,'availability_ttl':10800,'content_ttl':259200},NOW)
    assert result['availability']['state']=='fresh'
    assert result['content' if flag=='commercial_only' else 'price']['state']=='unknown'

def refresh_grant():
    from test_automation_execution import grant
    v=grant();v.update(purpose=cr.PURPOSE,commercial_only=True,discovery=False,explicit_safe_resume=True)
    for code,r in v['suppliers'].items():
        r['targets']=[];r['limits']={'REFRESH':0,'DISCOVERY':0};r['settings_after']={'enabled':False}
    r=v['suppliers']['kami']
    r['targets']=[{'supplier_code':'kami','supplier_id':r['supplier_id'],'source_product_id':i,'catalog_product_id':i,
        'confirmed_sterbrust_id':str(i),'external_id':str(i),'source_url':f'https://www.stanki.ru/product/{i}/'} for i in range(1,1271)]
    r['limits']['REFRESH']=1270;r['settings_after']['enabled']=True
    v['scope_sha256']=cr.digest([e for r in v['suppliers'].values() for e in r['targets']])
    v['matrix_sha256']=__import__('hashlib').sha256((cr.ROOT/'config/live11_approval_matrix.csv').read_bytes()).hexdigest()
    return v

def test_exact_approval_for_new_refresh_is_not_a_live11_recovery_or_broad_schedule(tmp_path,monkeypatch):
    from test_automation_execution import write,mocked_identity
    v=refresh_grant();p,sha=write(tmp_path,v);mocked_identity(monkeypatch)
    assert ex.load(p,sha)['purpose']==cr.PURPOSE
    with pytest.raises(PermissionError,match='EXACT_OPERATOR'):ex.load(p,sha,for_execution=True)
    assert ex.load(p,sha,sha,for_execution=True)['suppliers']['kami']['limits']['REFRESH']==1270
    v['suppliers']['kami']['targets'].pop();p,sha=write(tmp_path,v)
    with pytest.raises(PermissionError,match='1270'):ex.load(p,sha)

def test_card_outside_exact_refresh_is_rejected_before_any_writer(monkeypatch):
    card,cap=native();v=refresh_grant();r=v['suppliers']['partner_st']
    r['targets']=[{'external_id':card.external_id,'source_url':cap.requested_url,'source_product_id':7}]
    monkeypatch.setattr(ex,'ACTIVE',SimpleNamespace(value=v,sha='exact',check=lambda:None))
    assert cr.authorize_card(card,cap)['source_product_id']==7
    with pytest.raises(PermissionError,match='OUTSIDE'):cr.authorize_card(replace(card,external_id='unknown'),cap)
    with pytest.raises(PermissionError,match='OUTSIDE'):cr.authorize_card(card,replace(cap,final_url=cap.final_url+'redirect/'))

def test_old_batch_scope_not_used_by_single_refresh_scheduler(monkeypatch):
    from test_automation_execution import ScheduleDB,schedule
    db=ScheduleDB();s=schedule(monkeypatch,db);s.automation.value=refresh_grant()
    s.automation.value['suppliers']['kami']['supplier_id']=1
    # The fake DB contains an old partial LIVE11 result; commercial occurrence
    # queries must include the new exact SHA, never the old batch phase.
    queries=[]
    execute=db.execute
    def record(sql,args=None):queries.append(sql);execute(sql,args)
    db.execute=record
    assert cr.tick(s,datetime.now(timezone.utc))==[99]
    assert db.enqueued[0]['source_limit']==1270 and db.enqueued[0]['source_mode']=='REFRESH'
    assert all("approval_sha256'=%s" in q for q in queries)

@pytest.mark.parametrize('code',['beka_mak','intervesp','kami','vekprom','ltt_msk','ltt_sib','uss_stanko','partner_st','optimum'])
def test_commercial_writer_runs_existing_semantic_guard_without_delete(monkeypatch,code):
    import test_source_linked_update as linked
    from test_source_linked_update import backend,DB
    if code in ('partner_st','optimum'):
        card,cap=native()
        if code=='optimum':
            from test_optimum_source import HTML,URL
            from universal_supplier.control_plane.source_jobs import parse_existing
            card=parse_existing(code,HTML,URL);cap=replace(cap,requested_url=URL,final_url=URL)
        from universal_supplier.source_update import content_card
        monkeypatch.setattr(linked,'fixture',lambda source:(content_card(card),native_observation(card,cap),cap))
    repo,db,card,obs,cap=backend(monkeypatch,code)
    original=DB.execute
    def execute(self,sql,args=()):
        if sql.startswith('SELECT id FROM suppliers'):self.answer=(6,);return
        if sql.startswith('SELECT settings_snapshot'):self.answer=('exact',);return
        assert not sql.lstrip().startswith('DELETE')
        return original(self,sql,args)
    monkeypatch.setattr(DB,'execute',execute)
    monkeypatch.setattr(cr,'authorize_card',lambda *a:{'source_product_id':7,'catalog_product_id':1282})
    monkeypatch.setattr(ex,'require_active',lambda *a:SimpleNamespace(sha='exact'))
    # These two fixtures already contain ambiguous critical observations. Their
    # unchanged confirmed identity may refresh commerce, but changed semantic
    # content must still fail the existing guard (covered separately).
    description=card.description_text if code in ('ltt_msk','uss_stanko') else 'a changed description'
    fresh=replace(card,price=Decimal('123456'),old_price=None,currency='RUB',description_text=description)
    from universal_supplier.commercial import PriceState
    observed=replace(obs,price_state=PriceState.NUMERIC_PUBLIC,price=fresh.price,old_price=None,currency='RUB')
    result=repo._persist_passive_observation(fresh,observed,cap,source_run_id=12,commercial_only=True)
    assert result['observation_created'] and db.catalog==1282 and db.new_products==db.new_offers==0
    assert db.card.description_text==card.description_text and db.card.price==fresh.price
    repo._replace_details.assert_not_called()
    repo._record_field_observations.assert_not_called()
    repeat=repo._persist_passive_observation(fresh,observed,cap,source_run_id=12,commercial_only=True)
    assert repeat['exact_noop'] and len(db.captures)==len(db.observations)==1
    assert len(db.history)==1 and db.history[0][4]=='commercial'


@pytest.mark.parametrize('exception',[False,True])
def test_temporary_settings_restore_on_success_and_failure(tmp_path,monkeypatch,exception):
    from contextlib import contextmanager
    before={'enabled':False,'rate_settings':{'min_request_interval_seconds':20},'crawl_settings':{'saved':'keep'}}
    after={'enabled':True,'rate_settings':{'min_request_interval_seconds':2},'crawl_settings':{'saved':'keep','host_policy_approval':{'reference':'exact'}}}
    rule={'supplier_id':11,'settings_before':before,'settings_after':after,'targets':[{'source_product_id':7}]}
    v={'suppliers':{'beka_mak':rule},'protected_jobs':[[17,'untouched']]}
    class DB:
        row=list(before.values());answer=None;writes=0
        @contextmanager
        def transaction(self):
            saved=deepcopy(self.row)
            try:yield
            except BaseException:self.row=saved;raise
        def execute(self,sql,args=None):
            if sql.startswith('SELECT id,md5'):self.answer=[(17,'untouched')]
            elif sql.startswith('SELECT enabled'):self.answer=tuple(self.row)
            elif sql.startswith('UPDATE suppliers'):
                self.writes+=1;self.row=[args[0],args[1].obj,args[2].obj]
            else:raise AssertionError(sql)
            return self
        def fetchone(self):return self.answer
        def fetchall(self):return self.answer
    db=DB();checks=[];session=SimpleNamespace(config=None,value=v,folder=tmp_path,check_mappings=lambda c:checks.append(True))
    monkeypatch.setattr(cr,'scope_rows',lambda *a:None)
    monkeypatch.setattr('universal_supplier.control_plane.source_canary.validate_connection',lambda c:None)
    def run():
        with cr.settings(session,db):
            assert db.row==list(after.values())
            if exception:raise RuntimeError('critical job error')
    if exception:
        with pytest.raises(RuntimeError,match='critical job error'):run()
    else:run()
    assert db.row==list(before.values()) and db.writes==2 and checks
    assert json.loads((tmp_path/'SETTINGS_RESTORED.json').read_text())['SELECT_verified']


def test_frozen_refresh_snapshot_cannot_expand_to_discovery_or_other_targets():
    v=refresh_grant();rule=v['suppliers']['kami'];session=SimpleNamespace(value=v)
    snapshot={'supplier_code':'kami','automation':{'purpose':cr.PURPOSE,'phase':'REFRESH'},
        'source_input':{'entries':[{'source_product_id':e['source_product_id'],'source_external_id':e['external_id'],'url':e['source_url']} for e in rule['targets']],
            'mode':'REFRESH','engine_sha256':v['engine']}}
    cr.validate_snapshot(snapshot,session)
    bad=deepcopy(snapshot);bad['source_input']['entries'][0]['url']='https://www.stanki.ru/product/outside/'
    with pytest.raises(PermissionError,match='SCOPE'):cr.validate_snapshot(bad,session)
    bad=deepcopy(snapshot);bad['automation']['phase']='DISCOVERY'
    with pytest.raises(PermissionError,match='PURPOSE'):cr.validate_snapshot(bad,session)
    bad=deepcopy(snapshot);bad['source_batch']={'id':'old'}
    with pytest.raises(PermissionError,match='CANNOT_RECOVER'):cr.validate_snapshot(bad,session)


def test_existing_queue_preview_selects_approved_subset_not_first_catalog_urls(monkeypatch):
    from test_source_canary import FakeDB
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES','{}')
    db=FakeDB();supplier=db.supplier;selected=db.rows[8:22]
    v=refresh_grant();r=v['suppliers']['beka_mak'];r['supplier_id']=11
    r['targets']=[{'source_product_id':pk,'external_id':ext,'source_url':url} for pk,ext,url,total in selected]
    r['limits']['REFRESH']=14
    monkeypatch.setattr(ex,'ACTIVE',SimpleNamespace(sha='exact',value=v,check=lambda:None))
    preview=db.queue().preview_source_canary(11,14)
    assert preview['snapshot']['source_input']['entries']==[
        {'source_product_id':pk,'source_external_id':ext,'url':url} for pk,ext,url,total in selected]
    assert preview['snapshot']['source_input']['canary']['selected']==14
    assert not any('WITH known AS' in sql for sql,args in db.sql)
