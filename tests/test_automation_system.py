"""Configuration -> native pacing -> freshness -> two local preview artifacts."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json,re
from xml.etree import ElementTree as ET
import pytest
from fastapi.testclient import TestClient
from universal_supplier.control_plane.supplier_automation import settings_for,validate_settings,rate_for,due_occurrence,next_due
from universal_supplier.control_plane.rate_control import DomainRatePolicy,DomainRateLimiter
from universal_supplier.control_plane.source_batch import SOURCE_ORDER
from universal_supplier.offer_selection import OfferCandidate
from universal_supplier.commercial_freshness import assess
from universal_supplier.dual_preview import build,publish,latest

NOW=datetime(2026,10,26,14,tzinfo=timezone.utc)


def supplier(i,code):
    return {'id':i,'code':code,'name':code,'base_url':'https://example.test','enabled':True,
            'rate_settings':{},'crawl_settings':{}}


def snapshot():
    sources=[supplier(i,c) for i,c in enumerate(SOURCE_ORDER,1)]
    def row(source_id,source,offer_id):
        return {'source_product_id':source_id,'supplier_id':next(s['id'] for s in sources if s['code']==source),
            'supplier_code':source,'external_id':str(source_id),'external_id_is_stable':True,'offer_id':offer_id,
            'name':'Станок <пример>','own_model':'BF 20 L Vario','product_kind':'milling_machine','brand_raw':'OPTIMUM',
            'sku':'BF20','source_url':'https://example.test/product/','catalog_product_id':None,
            'confirmed_sterbrust_id':None,'identity_confirmed':False,'decision':'SAFE_NEW_PRODUCT_CANDIDATE',
            'match_status':'NEW_CANDIDATE','matching_current_for_capture':True,'match_conflicts':{},'conflicts':{},
            'identity_characteristics':{'power':'0.75 kW'},'blocking_review':False,
            'capture_id':offer_id,'capture_fingerprint':str(offer_id)*64,'http_status':200,
            'receipt_identity_valid':True,'observed_at':NOW-timedelta(minutes=5),
            'source_last_success_at':NOW-timedelta(minutes=5),'last_success_at':NOW-timedelta(minutes=5),
            'observed_price':Decimal('12000.50'),'price_state':'numeric_public','observed_currency':'RUB',
            'observed_availability':'in_stock','price_source':'saved receipt'}
    known=row(101,'partner_st',1);known.update(catalog_product_id=11,confirmed_sterbrust_id='10001',
        identity_confirmed=True,decision='EXACT_EXISTING',match_status='EXACT_MATCH',own_model='JWL-1443',brand_raw='JET')
    new=row(102,'optimum',2)
    c=OfferCandidate(catalog_product_id=11,offer_id=1,source_product_id=101,supplier_id=known['supplier_id'],
        offer_supplier_id=known['supplier_id'],supplier_code='partner_st',supplier_enabled=True,
        source_catalog_product_id=11,source_active=True,source_missed_crawls=0,
        source_last_success_at=known['source_last_success_at'],offer_active=True,offer_kind='default',
        offer_missed_crawls=0,offer_last_success_at=known['source_last_success_at'],
        latest_successful_full_crawl_at=NOW-timedelta(hours=1),price=Decimal('12000.50'),currency='RUB',
        availability='in_stock',availability_raw='В наличии',quantity=None)
    return {'complete':True,'as_of':NOW,'suppliers':sources,'records':[known,new],
        'registry':[{'sterbrust_product_id':'10001','catalog_product_id':11,'brand_raw':'JET','model_raw':'JWL-1443'}],
        'offer_candidates':{11:[c]},'details':{102:{'properties':[{'raw_name':'Мощность','raw_value':'0.75 кВт'}],
        'images':[{'source_url':'https://example.test/image.jpg','is_primary':True}]}}}


def test_all11_settings_are_independent_and_native_1to3_range():
    values=[settings_for(supplier(i,c)) for i,c in enumerate(SOURCE_ORDER,1)]
    changed=validate_settings({'delay_min_seconds':1,'delay_max_seconds':3,'max_concurrency':2},values[0])
    assert values[1]['delay_min_seconds']!=1 and values[0]['revision']==0
    rate=DomainRatePolicy.from_mapping(rate_for(changed))
    from test_host_fetch import Clock
    clock=Clock();limiter=DomainRateLimiter(rate,clock=clock,random_uniform=lambda a,b:b)
    starts=[]
    for _ in range(4):limiter.wait();starts.append(clock.monotonic())
    assert starts==[0,3,6,9]


@pytest.mark.parametrize('values',[{'delay_min_seconds':-1},{'delay_min_seconds':4,'delay_max_seconds':3},
    {'max_concurrency':9},{'retry_limit':0},{'price_ttl':0},{'request_timeout':'NaN'},
    {'timezone':'Invalid/Zone'},{'start_time':'25:00'},{'respect_retry_after':False}])
def test_invalid_or_protected_settings_are_rejected(values):
    with pytest.raises(ValueError):validate_settings(values,settings_for(supplier(1,'kami')))


def test_separate_phase_schedule_and_dst_daily_anchor():
    cfg=settings_for(supplier(1,'kami'));cfg.update(timezone='Europe/Berlin',start_time='09:00')
    due=due_occurrence(cfg,'DISCOVERY',datetime(2026,10,24,12,tzinfo=timezone.utc))
    nxt=next_due(cfg,'DISCOVERY',datetime(2026,10,24,12,tzinfo=timezone.utc),due)
    assert due.hour==7 and nxt.hour==8 and nxt-due==timedelta(hours=25)
    assert due_occurrence(cfg,'REFRESH',NOW)!=due_occurrence(cfg,'DISCOVERY',NOW)


def test_two_previews_commercial_whitelist_and_determinism(tmp_path):
    value=snapshot();raw,manifest=build(value)
    assert manifest['counts']=={'updates':1,'new_products':1}
    assert build(deepcopy(value))==(raw,manifest)
    updates=ET.fromstring(raw['updates']);product=updates.find('product')
    assert product.attrib=={'sterbrust_product_id':'10001'}
    assert {x.tag for x in product}=={'price','currency','availability'}
    new=ET.fromstring(raw['new_products']).find('product')
    assert new.attrib['candidate_id'].startswith('NEWC-') and not new.findall('.//sterbrust_product_id')
    assert new.find('content/name').text=='Станок <пример>'
    publish(tmp_path,raw,manifest);assert latest(tmp_path,'updates')==raw['updates']


@pytest.mark.parametrize('field,value',[('observed_price',None),('observed_price',0),
    ('observed_availability','unknown'),('receipt_identity_valid',False),('http_status',503),
    ('identity_confirmed',False),('blocking_review',True)])
def test_unproven_commercial_or_identity_never_leaks_into_updates(field,value):
    data=snapshot();data['records'][0][field]=value
    raw,m=build(data);assert m['counts']['updates']==0
    assert not ET.fromstring(raw['updates']).findall('product')


def test_field_ttls_preserve_history_and_omit_unknown_commercial():
    data=snapshot();row=data['records'][1];row['observed_at']=NOW-timedelta(hours=7)
    before=deepcopy(row);raw,m=build(data)
    product=ET.fromstring(raw['new_products']).find('product')
    assert product.find('commercial/price').text is None
    assert product.find('commercial/availability_normalized').text is None
    assert row==before and assess(row,settings_for(data['suppliers'][0]),NOW)['price']['state']=='stale'


def test_cross_supplier_dedupe_and_technical_conflict():
    data=snapshot();other=deepcopy(data['records'][1]);other.update(source_product_id=103,offer_id=3,
        supplier_code='vekprom',external_id='102',supplier_id=3,capture_id=3)
    data['records'].append(other);raw,m=build(data)
    assert m['counts']['new_products']==1
    node=ET.fromstring(raw['new_products']).find('product')
    assert len(json.loads(node.find('supplier_aliases').text))==2
    other['identity_characteristics']={'power':'2 kW'}
    assert build(data)[1]['counts']['new_products']==0


def test_registry_review_pending_reference_and_partial_snapshot_fail_closed():
    for update in ({'blocking_review':True},{'match_status':'CONFLICT'},
                   {'matching_current_for_capture':False},{'catalog_product_id':22}):
        value=snapshot();value['records'][1].update(update)
        assert build(value)[1]['counts']['new_products']==0
    value=snapshot();value['records'][1]['supplier_code']='beka_mak_tr'
    assert build(value)[1]['counts']['new_products']==0
    value=snapshot();value['registry'].append({'sterbrust_product_id':'20000','brand_raw':'OPTIMUM','model_raw':'BF 20 L Vario'})
    assert build(value)[1]['counts']['new_products']==0
    value['complete']=False
    with pytest.raises(ValueError,match='INCOMPLETE'):build(value)


def test_broken_new_version_cannot_replace_last_correct_preview(tmp_path):
    raw,manifest=build(snapshot());publish(tmp_path,raw,manifest);before=latest(tmp_path,'updates')
    corrupt={**raw,'new_products':b'<broken'}
    with pytest.raises(ET.ParseError):publish(tmp_path,corrupt,manifest)
    assert latest(tmp_path,'updates')==before

def test_confirmed_new_card_leaves_candidate_feed_and_uses_original_product_id():
    value=snapshot();r=value['records'][1]
    r.update(catalog_product_id=22,confirmed_sterbrust_id='20002',identity_confirmed=True,
             decision='EXACT_EXISTING',match_status='MANUAL_CONFIRMED')
    value['registry'].append({'sterbrust_product_id':'20002','catalog_product_id':22,
                             'brand_raw':r['brand_raw'],'model_raw':r['own_model']})
    candidate=replace(value['offer_candidates'][11][0],catalog_product_id=22,offer_id=2,
        source_product_id=102,supplier_id=r['supplier_id'],offer_supplier_id=r['supplier_id'],
        supplier_code=r['supplier_code'],source_catalog_product_id=22)
    value['offer_candidates'][22]=[candidate]
    raw,manifest=build(value)
    assert manifest['counts']=={'updates':2,'new_products':0}
    ids={p.attrib['sterbrust_product_id'] for p in ET.fromstring(raw['updates']).findall('product')}
    assert ids=={'10001','20002'}

def test_execution_variants_and_cross_supplier_sku_do_not_collapse():
    value=snapshot();other=deepcopy(value['records'][1])
    other.update(source_product_id=103,offer_id=3,capture_id=3,supplier_code='vekprom',
        supplier_id=6,external_id='102',own_model='BF 20 L Vario NC')
    value['records'].append(other)
    assert build(value)[1]['counts']['new_products']==2

def test_synthetic_capture_never_becomes_fresh_live_data_or_feed_candidate():
    value=snapshot()
    for r in value['records']:r['capture_diagnostics']={'scenario':'B0_SQL_QA_SYNTHETIC_NOT_HTTP'}
    raw,m=build(value)
    assert m['counts']=={'updates':0,'new_products':0}
    assert not assess(value['records'][0],settings_for(value['suppliers'][0]),NOW)['receipt_valid']

def test_pending_matching_never_updates_confirmed_product_commercial_fields():
    value=snapshot();value['records'][0]['matching_current_for_capture']=False
    assert build(value)[1]['counts']['updates']==0

def test_source_quarantine_blocks_both_feeds_until_a_later_verified_capture():
    from universal_supplier.preview_store import mark_quarantine
    value=snapshot();runs=[]
    for row in value['records']:
        runs.append({'code':row['supplier_code'],'started_at':NOW,'runtime':{'quarantined':[
            {'external_id':row['external_id'],'url':row['source_url'],'original_capture_metadata':{'observed_at':NOW.isoformat()}}]}})
    mark_quarantine(value['records'],runs)
    assert build(value)[1]['counts']=={'updates':0,'new_products':0}
    # A later successful observation supersedes the rejection; no SQL/history mutation here.
    for row in value['records']:row['source_last_success_at']=NOW+timedelta(minutes=1)
    mark_quarantine(value['records'],runs)
    assert not any(r['blocking_quarantine'] for r in value['records'])
