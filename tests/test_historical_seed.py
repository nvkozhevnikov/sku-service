import hashlib,json
import pytest
from competitor_intel.historical_seed import metadata,export_corpus,plan_refresh,historical_context

def row(url='https://www.stanki.ru/catalog/test/',capture='2026-10-05T10:00:00+00:00'):
    return {'card':{'canonical_url':url,'external_id':'source:1','name':'Станок A+','brand':'BRAND','price':100000,
                    'raw_data':{'full_model':'A+','card_kind':'SINGLE_PRODUCT','identity_status':'SOURCE_MODEL_OBSERVED'},'categories':[{'name':'Станки','position':0}]},
            'capture':{'observed_at':capture,'sha256':'a'*64},'evidence_ref':'saved.capture'}

def test_historical_never_live_or_price():
    s=metadata(row(),1)
    assert s['evidence_class']=='HISTORICAL_SEED' and not s['is_live'] and s['price'] is None
    assert s['original_capture_time']=='2026-10-05T10:00:00+00:00' and s['model']=='A+'
    assert not s['new_product_event_eligible'] and not s['live_price_eligible']

@pytest.mark.parametrize('value',[None,'bad','2026-10-05T10:00:00'])
def test_missing_time_not_replaced_by_today(value):
    assert metadata(row(capture=value),1)['original_capture_time'] is None

def test_no_age_policy_no_global_refresh():
    seeds=[metadata(row(),1)]
    assert plan_refresh(seeds)['selected']==[]
    assert plan_refresh(seeds,stale_before='2026-10-12T00:00:00+00:00')['selected']==[]

def test_age_applies_only_tracked_subset():
    a=metadata(row(),1); b=metadata(row('https://www.stanki.ru/catalog/other/'),2)
    q=plan_refresh([a,b],tracked_urls=[a['product_url']],stale_before='2026-10-12T00:00:00+00:00')
    assert [s['url'] for s in q['selected']]==[a['product_url']]

def test_campaign_products_only_bounded_queue():
    urls=['https://www.stanki.ru/catalog/'+str(n)+'/' for n in range(3)]
    q=plan_refresh([],campaign_urls=urls,max_products=2)
    assert len(q['selected'])==2 and len(q['deferred'])==1 and not q['full_catalog_crawl']

def test_no_brand_fallback_to_supplier():
    r=row();r['card']['brand']=None
    assert metadata(r,1)['brand'] is None

def test_export_input_hash_and_repeat_idempotency(tmp_path):
    path=tmp_path/'source.jsonl'; data=(json.dumps(row())+'\n').encode();path.write_bytes(data)
    sha=hashlib.sha256(data).hexdigest(); out=tmp_path/'output'
    first=export_corpus(path,out,sha); before=(out/'HISTORICAL_SEEDS.jsonl').read_bytes()
    second=export_corpus(path,out,sha)
    assert path.read_bytes()==data and before==(out/'HISTORICAL_SEEDS.jsonl').read_bytes()
    assert first['output_sha256']==second['output_sha256'] and first['source_unchanged']

def test_pinned_hash_fail_closed(tmp_path):
    path=tmp_path/'source.jsonl';path.write_text(json.dumps(row())+'\n')
    with pytest.raises(ValueError,match='PINNED'): export_corpus(path,tmp_path/'out','bad')

def test_source_path_not_overwritten(tmp_path):
    path=tmp_path/'source.jsonl';path.write_text(json.dumps(row())+'\n')
    with pytest.raises(ValueError): export_corpus(path,tmp_path)

def test_group_model_stays_reported_not_exact_identity():
    r=row();r['card']['raw_data']['card_kind']='SERIES'
    s=metadata(r,1);assert s['card_kind']=='SERIES' and not s['new_product_event_eligible']

def test_historical_context_does_not_replace_live_fields():
    s=metadata(row(),1); live={'product_url':s['product_url'],'product_name':'Актуальное название','brand':None,'price':90000,'observed_at':'2026-10-12T10:00:00+00:00'}
    result=historical_context(live,[s])
    assert all(result[k]==v for k,v in live.items())
    assert result['historical_context'][0]['brand']=='BRAND'
    assert result['historical_context'][0]['original_capture_time'].startswith('2026-10-05')
