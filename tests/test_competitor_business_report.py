from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import csv
import json
import re
import pytest
from competitor_intel.change_detection import detect_events,event_key,day
from competitor_intel.business_report import build_facts,export_preview,CSV_SCHEMAS,safe_url
from competitor_intel.business_html import render_html

SOURCE={'code':'future_factory','name':'Новый конкурент','region':'Тестовый регион','capabilities':['PROMOTIONS','PRODUCT_PRICES']}

def item(name='Станок A',price=100000,role='PUBLIC',old=None,url=None,priority='HIGH'):
    return {'product_name':name,'product_url':url or 'https://future.example/products/'+name[-1]+'/',
            'new_price':str(price) if price is not None else None,'old_price':str(old) if old is not None else None,
            'price_type':role,'currency':'RUB','priority':priority,'brand':'BRAND','model':name[-1],
            'category':'Токарные станки','region':'Тестовый регион','campaign_name':'Сезон','tab_title':None}

def snap(version='a',at='2026-10-01T10:00:00+03:00',items=None,kind='PROMOTION',entity=None,end='2026-10-31'):
    return {'source':SOURCE['code'],'entity':entity or 'https://future.example/campaign/','version':version,'extractor':'fixture-v1',
            'observed_at':at,'page_type':kind,'title':'Сезонные цены','published_at':'2026-10-01','valid_from':'2026-10-01','valid_to':end,
            'text':'Скидки на станки','mechanics':['Скидка'],'items':[item()] if items is None else items,
            'item_scope_complete':True,'source_discovery_complete':True}

def dataset(snaps,current=None,coverage=None,analysis=None,sources=None):
    last={}
    for s in snaps:last[(s['source'],s['entity'])]=s
    return {'sources':sources or [SOURCE],'history':snaps,'current':current or list(last.values()),'coverage':coverage or [],'analysis':analysis or []}

def kinds(data,day='2026-10-09'): return [e['event_type'] for e in detect_events(data,day)]

def test_baseline_is_not_new_products_or_promotions():
    assert kinds(dataset([snap()]))==['BASELINE_OBSERVATION']
    f=build_facts(dataset([snap()]),'2026-10-01','2026-10-09')
    assert f['summary']['observation_mode']=='INITIAL BASELINE'
    assert f['summary']['new_products'] is None and f['summary']['price_decreases'] is None

def test_identical_observation_no_changed_event():
    a=snap(); b={**a,'observed_at':'2026-10-02T10:00:00+03:00'}
    assert kinds(dataset([a,b]))==['BASELINE_OBSERVATION']

@pytest.mark.parametrize('before,after,event,percent',[(100000,90000,'PRICE_DECREASE',Decimal('-10.00')),(90000,95000,'PRICE_INCREASE',Decimal('5.56'))])
def test_exact_public_price_delta(before,after,event,percent):
    a=snap(items=[item(price=before)]); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=after)])
    e=next(e for e in detect_events(dataset([a,b]),'2026-10-09') if e['event_type']==event)
    assert (e['old_observed_price'],e['new_observed_price'],e['absolute_delta'],e['percent_delta'])==(before,after,abs(before-after),percent)
    assert all(e[k] for k in ['source','product_url','brand','model','category','priority','campaign_context','observed_before','observed_after'])

@pytest.mark.parametrize('role',['AUTH_REQUIRED','PERSONAL','FROM_PRICE','INSTALLMENT_MONTHLY','CREDIT_PAYMENT','BONUS','UNKNOWN'])
def test_non_public_transition_never_public_price_increase(role):
    a=snap(items=[item(price=100000,role='PUBLIC')]); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=120000,role=role)])
    types=kinds(dataset([a,b])); assert 'PRICE_SEMANTICS_CHANGED' in types
    assert 'PRICE_INCREASE' not in types and 'PRICE_DECREASE' not in types

def test_same_personal_type_price_change_requires_review():
    a=snap(items=[item(price=100000,role='PERSONAL')]); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=90000,role='PERSONAL')])
    assert 'PRICE_REVIEW' in kinds(dataset([a,b])) and 'PRICE_DECREASE' not in kinds(dataset([a,b]))

def test_currency_semantics():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',[{**item(price=90000),'currency':'USD'}])
    assert 'PRICE_SEMANTICS_CHANGED' in kinds(dataset([a,b])) and 'PRICE_DECREASE' not in kinds(dataset([a,b]))

def test_public_to_sale_is_comparable():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=90000,role='SALE',old=100000)])
    assert {'PRICE_DECREASE','OLD_PRICE_APPEARED','DISCOUNT_CHANGED'}<=set(kinds(dataset([a,b])))

def test_old_price_removed():
    a=snap(items=[item(price=90000,role='SALE',old=100000)]); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=90000)])
    assert {'OLD_PRICE_REMOVED','DISCOUNT_CHANGED'}<=set(kinds(dataset([a,b])))

def test_new_promotion_requires_comparable_discovery():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',entity='https://future.example/new/')
    coverage=[{'source':SOURCE['code'],'observed_at':a['observed_at'],'complete':True,'entities':[a['entity']]}]
    assert 'NEW_PROMOTION' in kinds(dataset([a,b],coverage=coverage))
    assert 'NEW_PROMOTION' not in kinds(dataset([a,b]))

def test_promotion_deadline_changed():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',end='2026-11-30')
    e=next(e for e in detect_events(dataset([a,b]),'2026-10-09') if e['event_type']=='PROMOTION_CHANGED')
    assert e['relevant_values']['changed_fields']==['valid_to']
    assert e['relevant_values']['end_date_before']=='2026-10-31' and e['relevant_values']['end_date_after']=='2026-11-30'

def test_added_removed_products_exact_events():
    a=snap(items=[item('Станок A'),item('Станок B')]); b=snap('b','2026-10-02T10:00:00+03:00',[item('Станок A'),item('Станок C')])
    types=kinds(dataset([a,b])); assert Counter(types)==Counter(['BASELINE_OBSERVATION','PROMOTION_CHANGED','NEW_CAMPAIGN_PRODUCT','NEW_PRODUCT_OBSERVED','CAMPAIGN_PRODUCT_REMOVED','PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN'])

from collections import Counter

def test_partial_capture_does_not_prove_removal():
    a=snap(items=[item('Станок A'),item('Станок B')]); b=snap('b','2026-10-02T10:00:00+03:00',[item('Станок A')]); b['item_scope_complete']=False
    assert 'CAMPAIGN_PRODUCT_REMOVED' not in kinds(dataset([a,b]))

def test_scope_expansion_from_partial_is_not_new_product():
    a=snap(); a['item_scope_complete']=False
    b=snap('b','2026-10-02T10:00:00+03:00',[item('Станок A'),item('Станок B')])
    assert 'NEW_CAMPAIGN_PRODUCT' not in kinds(dataset([a,b]))
    assert 'NEW_PRODUCT_OBSERVED' not in kinds(dataset([a,b]))

def test_different_rendered_scopes_do_not_prove_membership_changes():
    a=snap(); a['scope_key']='first-page'
    b=snap('b','2026-10-02T10:00:00+03:00',[item('Станок A'),item('Станок B')]); b['scope_key']='full-campaign'
    assert 'NEW_CAMPAIGN_PRODUCT' not in kinds(dataset([a,b])) and 'PROMOTION_CHANGED' not in kinds(dataset([a,b]))

def test_new_campaign_existing_product_not_new_model():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',entity='https://future.example/new-campaign/')
    coverage=[{'source':SOURCE['code'],'observed_at':a['observed_at'],'complete':True,'entities':[a['entity']]}]
    assert 'NEW_PROMOTION' in kinds(dataset([a,b],coverage=coverage))
    assert 'NEW_PRODUCT_OBSERVED' not in kinds(dataset([a,b],coverage=coverage))

def test_future_normalized_scope_metadata_read_generically():
    from competitor_intel.business_data import normalized_dataset
    s=snap()
    state={'competitor_sources':[{**SOURCE,'id':1}],
           'competitor_pages':[{'id':1,'source_id':1,'canonical_url':s['entity'],'current_content_hash':'a','last_seen_at':s['observed_at']}],
           'competitor_page_versions':[{'id':1,'page_id':1,'content_hash':'a','extractor_version':'fixture','qa_status':'ACCEPTED','fetched_at':s['observed_at'],'payload':s}],
           'competitor_campaign_items':[{**item(),'page_version_id':1}],'competitor_observations':[],'competitor_ai_analysis':[],
           'competitor_runs':[{'result':{'coverage':[{'source':SOURCE['code'],'observed_at':s['observed_at'],'complete':True,'entities':[s['entity']]}]}}]}
    d=normalized_dataset(state)
    assert d['current'][0]['item_scope_complete'] is True and d['current'][0]['mechanics']==s['mechanics']
    assert d['coverage'][0]['source']==SOURCE['code']

def test_adapter_metadata_opt_in_does_not_change_legacy_defaults():
    from competitor_intel.model import Page
    p=Page('future','https://future.example/','https://future.example/','Сезон','PROMOTION','Raw','Normalized')
    initial=p.payload(); assert 'item_scope_complete' not in initial
    p.item_scope_complete=True; p.commercial_mechanics=['Скидка']
    assert p.payload()['item_scope_complete'] is True and p.payload()['commercial_mechanics']==['Скидка']

def test_expired_baseline_does_not_claim_ended_today():
    a=snap(at='2026-10-09T10:00:00+03:00',end='2026-10-04')
    assert kinds(dataset([a]))==['BASELINE_OBSERVATION']
    assert build_facts(dataset([a]),'2026-10-01','2026-10-09')['promotions'][0]['status']=='ENDED'

def test_campaign_ended_after_known_active_observation():
    a=snap(end='2026-10-04')
    assert 'PROMOTION_ENDED' in kinds(dataset([a]))

@pytest.mark.parametrize('end,expected',[('2026-10-09',0),('2026-10-12',3),('2026-10-16',7)])
def test_ending_soon_boundaries(end,expected):
    e=next(e for e in detect_events(dataset([snap(end=end)]),'2026-10-09') if e['event_type']=='PROMOTION_ENDING_SOON')
    assert e['relevant_values']['days_remaining']==expected
    assert e['relevant_values']['urgency']==('3_DAYS' if expected<=3 else '7_DAYS')

def test_unknown_deadline_and_eight_days_not_ending():
    assert 'PROMOTION_ENDING_SOON' not in kinds(dataset([snap(end=None)]))
    assert 'PROMOTION_ENDING_SOON' not in kinds(dataset([snap(end='2026-10-17')]))

def test_moscow_calendar_at_utc_day_boundary():
    assert day('2026-10-08T22:30:00Z')==date(2026,10,9)

def test_mixed_timezone_observations_are_chronological():
    early=snap(at='2026-10-02T01:00:00+03:00',items=[item(price=100000)])
    later=snap('b','2026-10-01T23:00:00Z',[item(price=90000)])
    assert 'PRICE_DECREASE' in kinds(dataset([later,early]))
    assert 'PRICE_INCREASE' not in kinds(dataset([later,early]))

def test_news_new_requires_coverage():
    a=snap(); news=snap('news','2026-10-02T10:00:00+03:00',[],kind='NEWS',entity='https://future.example/news/one/')
    coverage=[{'source':SOURCE['code'],'observed_at':a['observed_at'],'complete':True,'entities':[a['entity']]}]
    assert 'NEW_NEWS' in kinds(dataset([a,news],coverage=coverage))

def test_changed_news():
    a=snap(kind='NEWS',items=[]); b={**a,'version':'b','text':'Обновлённый исходный материал','observed_at':'2026-10-02T10:00:00+03:00'}
    assert 'NEWS_CHANGED' in kinds(dataset([a,b]))

def test_homepage_blocks_appear_and_disappear():
    a=snap(kind='HOMEPAGE_BLOCK',items=[{**item(),'campaign_name':'Зима'}]); b=snap('b','2026-10-02T10:00:00+03:00',[{**item(),'campaign_name':'Осень'}],kind='HOMEPAGE_BLOCK')
    assert {'HOMEPAGE_PROMO_APPEARED','HOMEPAGE_PROMO_REMOVED'}<=set(kinds(dataset([a,b])))

def test_parser_change_is_new_baseline_not_price_drop():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=90000)]); b['extractor']='fixture-v2'
    assert kinds(dataset([a,b])).count('BASELINE_OBSERVATION')==2
    assert 'PRICE_DECREASE' not in kinds(dataset([a,b]))

def test_idempotent_event_identity_and_return_to_prior_version():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',[item(price=90000)]); a2={**a,'observed_at':'2026-10-03T10:00:00+03:00'}; b2={**b,'observed_at':'2026-10-04T10:00:00+03:00'}
    es=detect_events(dataset([a,b,a2,b2]),'2026-10-09')
    drops=[e for e in es if e['event_type']=='PRICE_DECREASE']
    assert len(drops)==2 and drops[0]['event_id']!=drops[1]['event_id']
    assert es==detect_events(dataset([a,b,a2,b2]),'2026-10-09')
    assert len({e['event_id'] for e in es})==len(es)

def test_price_event_before_after_values_affect_key():
    base={'source':'future','entity':'https://future/','event_type':'PRICE_DECREASE','before_version':'a','after_version':'b','before':100000,'after':90000}
    assert event_key(base)!=event_key({**base,'after':80000})

def test_source_isolation_and_future_sources_generic():
    a=snap(); b={**snap('b','2026-10-02T10:00:00+03:00',[item(price=90000)]),'source':'other_factory'}
    sources=[SOURCE,{**SOURCE,'code':'other_factory','name':'Другой конкурент','capabilities':['PRODUCT_PRICES']}]
    f=build_facts(dataset([a,b],sources=sources),'2026-10-01','2026-10-09')
    assert len(f['sources'])==2 and not f['price_changes']
    assert set(s['code'] for s in f['sources'])=={'future_factory','other_factory'}

def test_discount_stats_special_prices_excluded_and_contexts_deduplicated():
    a=snap(items=[item(price=90000,role='SALE',old=100000),item('Станок B',price=10000,role='AUTH_REQUIRED',old=100000)])
    b={**a,'entity':'https://future.example/duplicate-context/'}
    f=build_facts(dataset([a,b]),'2026-10-01','2026-10-09')
    assert f['summary']['discounted_public_products']==1 and f['summary']['discount_stats']['mean']==10
    assert sum(b['count'] for b in f['distribution'])==1

def test_sale_cards_counted_without_sale_page_source_specific_rule():
    a=snap(kind='CAMPAIGN',items=[item(price=90000,role='SALE',old=100000)])
    f=build_facts(dataset([a]),'2026-10-01','2026-10-09')
    assert f['sources'][0]['sale_items']==1

def test_ambiguous_current_prices_excluded_from_stats():
    a=snap(items=[item(price=90000,role='SALE',old=100000)]); b={**snap(items=[item(price=80000,role='SALE',old=100000)]),'entity':'https://future.example/other/'}
    f=build_facts(dataset([a,b]),'2026-10-01','2026-10-09')
    assert f['summary']['ambiguous_price_contexts']==1 and f['summary']['discounted_public_products']==0

def test_csv_schema_html_and_export_idempotency(tmp_path):
    a=snap(items=[item(price=90000,role='SALE',old=100000)])
    one=export_preview(dataset([a]),'2026-10-01','2026-10-09',tmp_path)
    files=['index.html','SUMMARY.json','EVENTS.json']+list(CSV_SCHEMAS)
    before={f:(tmp_path/f).read_bytes() for f in files}
    two=export_preview(dataset([a]),'2026-10-01','2026-10-09',tmp_path)
    assert two['new_events']==0 and two['duplicate_events']==0 and one['business_facts_sha256']==two['business_facts_sha256']
    assert before=={f:(tmp_path/f).read_bytes() for f in files}
    for f,cols in CSV_SCHEMAS.items():
        with (tmp_path/f).open(encoding='utf-8-sig',newline='') as handle: assert next(csv.reader(handle))==cols
    html=(tmp_path/'index.html').read_text(encoding='utf8')
    assert 'lang="ru"' in html and 'target="_blank"' in html and 'INITIAL BASELINE' in html
    assert 'src="https://' not in html and 'href="https://cdn' not in html

def test_ai_absent_and_present_fixture(tmp_path):
    a=snap(); f=build_facts(dataset([a]),'2026-10-01','2026-10-09')
    assert f['analysis']==[] and f['summary']['ai_network_calls']==0
    ai={'source':SOURCE['code'],'entity':a['entity'],'summary':'Сохранённый анализ','content_type':'PRICE_CUT','commercial_mechanic_json':['Скидка'],
        'strategic_signals_json':['Сохранённый сигнал'],'sterbrust_relevance':'HIGH','provider':'fixture-provider','model':'fixture-model'}
    f=build_facts(dataset([a],analysis=[ai]),'2026-10-01','2026-10-09')
    assert f['analysis']==[ai] and f['summary']['ai_network_calls']==0
    assert 'fixture-provider' in render_html(json.loads(json.dumps(f,default=str)))

def test_xss_data_cannot_break_json_script(tmp_path):
    a=snap(); a['title']='</script><img src=x onerror=alert(1)>'
    export_preview(dataset([a]),'2026-10-01','2026-10-09',tmp_path)
    html=(tmp_path/'index.html').read_text(encoding='utf8')
    assert '</script><img' not in html and '\\u003c/script\\u003e' in html
    assert safe_url('javascript:alert(1)') is None and safe_url('https://user:secret@example.com/') is None

def test_daily_report_period_news_filter():
    a=snap(kind='NEWS',items=[]); a['published_at']='2026-08-28'
    assert build_facts(dataset([a]),'2026-09-01','2026-10-09')['news']==[]

def test_high_industrial_price_drop_ranks_first():
    a=snap(items=[item('Станок A',priority='LOW'),item('Станок B',priority='HIGH')]); b=snap('b','2026-10-02T10:00:00+03:00',[item('Станок A',price=90000,priority='LOW'),item('Станок B',price=90000,priority='HIGH')])
    meaningful=[e for e in detect_events(dataset([a,b]),'2026-10-09') if e['event_type']!='BASELINE_OBSERVATION']
    assert meaningful[0]['event_type']=='PRICE_DECREASE' and meaningful[0]['priority']=='HIGH'

def test_different_region_never_direct_price_comparison():
    a=snap(); b=snap('b','2026-10-02T10:00:00+03:00',[{**item(price=90000),'region':'Другой регион'}])
    assert 'PRICE_DECREASE' not in kinds(dataset([a,b]))

def test_preview_cli_never_dispatches_crawl_or_analyze(monkeypatch,tmp_path):
    import sys
    from competitor_intel import __main__ as cli
    from competitor_intel import business_report
    calls=[]
    class FakeStore:
        def close(self): calls.append('close')
    monkeypatch.setattr(cli,'Store',lambda dsn:FakeStore())
    monkeypatch.setattr(cli,'crawl',lambda *a,**k: (_ for _ in ()).throw(AssertionError('crawl')))
    monkeypatch.setattr(cli,'analyze_pending',lambda *a,**k: (_ for _ in ()).throw(AssertionError('AI')))
    monkeypatch.setattr(business_report,'preview_from_store',lambda *a,**k:(calls.append('preview') or {'mode':'read-only'}))
    monkeypatch.setattr(sys,'argv',['competitor_intel','report','--preview','--through','2026-10-09','--output',str(tmp_path)])
    cli.main()
    assert calls==['preview','close']

def test_snapshot_reader_transaction_is_read_only():
    from contextlib import nullcontext
    from competitor_intel.business_data import read_state,TABLES
    statements=[]
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params=()): statements.append(sql)
        def fetchone(self): return {'name':'allowed_table'}
        def fetchall(self): return []
    class Connection:
        def transaction(self): return nullcontext()
        def cursor(self): return Cursor()
    store=type('Store',(),{'conn':Connection()})()
    assert set(read_state(store))==set(TABLES)
    assert statements[0]=='SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert all(s.startswith(('SELECT ','SET TRANSACTION ')) for s in statements)
