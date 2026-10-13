from datetime import datetime,timezone,date
from competitor_intel.transport import Capture
from competitor_intel.five_source_extract import extract,content_kind,key
from competitor_intel.model import Page,Item
from competitor_intel.change_detection import detect_events

def cap(url,html):return Capture(url,url,html,'fixture.html','fixture-hash',datetime(2026,10,13,tzinfo=timezone.utc),'FIXTURE_ONLY')

def test_jettools_source_novelty_not_system_event():
    html='<h1>Новинки</h1><div class="component-item-product"><a class="title" href="/catalog/new/jsg/">JET JSG-48EVS Станок</a><div class="component-catalog-price"><div>40 000 ₽</div></div><div class="component-product-vendor-code">JR124</div><div class="tooltips-wrapper">Новинка</div></div>'
    p=extract('jettools_ru',cap('https://www.jettools.ru/catalog/new/',html),'NOVELTY_INDEX')
    assert p.items[0].new_price==40000 and p.item_metadata[key(p.items[0].product_url,None)]['source_new_label']=='Новинка'
    assert p.item_metadata[key(p.items[0].product_url,None)]['sku']=='JR124'
    snap={'source':'jettools_ru','entity':p.canonical_url,'page_type':p.page_type,'title':p.title,'items':[{'product_name':'JET JSG-48EVS','product_url':p.items[0].product_url,'price_type':'PUBLIC','new_price':40000}],'version':'one','extractor':'five1','observed_at':'2026-10-13T00:00:00Z'}
    assert {e['event_type'] for e in detect_events({'history':[snap],'coverage':[]},'2026-10-13')}=={'BASELINE_OBSERVATION'}

def test_rustan_article_and_gift_not_price_decrease():
    p=extract('rustan',cap('https://rustan.ru/articles/example','<h1>Как выбрать станок</h1><time datetime="2026-09-29"></time><main>Подарок в комплекте</main>'),'BLOG')
    assert p.page_type=='BLOG' and p.published_at==date(2026,9,29) and not p.items
    assert content_kind('rustan','https://rustan.ru/articles/example','NEWS')=='BLOG'

def test_belmash_taxonomy_separate():
    for segment,expected in [('sales','PROMOTION'),('latest','NEW_PRODUCT_ANNOUNCEMENT'),('main','NEWS'),('expo','EVENT'),('upd','MODERNIZATION'),('polezno','CONTENT')]:
        assert content_kind('belmash','https://belmash.ru/novosti/'+segment+'/example/','NEWS')==expected

def test_belmash_saving_percent_without_old_price_not_manufactured():
    html='<h1>Акция</h1><div class="tf-card"><a class="d-flex" href="/products/stanok/">BELMASH Станок</a><div class="cip__new-price">10 000 ₽</div><div class="cip__profit">Экономия 2 000 ₽</div><div class="tf-cis">-20%</div></div>'
    p=extract('belmash',cap('https://belmash.ru/products/top-offers/current/',html),'PROMOTION')
    i=p.items[0];assert i.new_price==10000 and i.old_price is None and i.discount_amount is None
    assert i.displayed_discount_raw=='-20%' and p.item_metadata[key(i.product_url,None)]['displayed_saving_raw']=='Экономия 2 000 ₽'

def test_harvey_standard_vs_outlet_never_false_price_decrease():
    common={'source':'harvey_rus','entity':'https://harvey-rus.ru/catalog/item/','page_type':'SALE_PAGE','title':'Harvey','extractor':'five1','item_scope_complete':False}
    item={'product_url':'https://harvey-rus.ru/catalog/item/','product_name':'Harvey A','region':'Россия','currency':'RUB','price_type':'SALE','new_price':100000,'tab_title':None}
    before={**common,'version':'a','observed_at':'2026-10-12T10:00:00Z','items':[item]}
    after={**common,'version':'b','observed_at':'2026-10-13T10:00:00Z','items':[{**item,'new_price':60000,'tab_title':'CONDITION:OUTLET'}]}
    assert not any(e['event_type']=='PRICE_DECREASE' for e in detect_events({'history':[before,after],'coverage':[]},'2026-10-13'))
    html='<h1>Дисконт</h1><div class="new-catalog-sect__list-item"><a class="new-catalog-sect__list-name" href="/catalog/discount/a-341p/">Harvey A Дисконт (341П)</a><div class="new-catalog-sect__list-price">60 000 ₽<span class="base__price">100 000 ₽</span></div></div>'
    p=extract('harvey_rus',cap('https://harvey-rus.ru/catalog/discount/',html),'OUTLET_INDEX')
    assert p.items[0].tab_title=='CONDITION:OUTLET' and p.items[0].old_price==100000

def test_amur_ambiguous_values_and_commercial_offer_not_unit_prices():
    html='<h1>Акция</h1><div class="product-card"><a class="product-card-title" href="/catalog/item/">CERTON Станок</a><div class="product-card-price__value">100 000 ₽ 90 000 ₽</div></div>'
    p=extract('amur_stan',cap('https://amur-stan.ru/discount/current/',html),'PROMOTION')
    assert p.items[0].price_type=='UNKNOWN' and p.items[0].new_price is None and p.items[0].old_price is None
    assert len(p.item_metadata[key(p.items[0].product_url,None)]['ambiguous_commercial_values'])==2

def test_optional_metadata_does_not_change_old_source_hashes():
    p=Page('metalmaster','https://metalmaster.ru/','https://metalmaster.ru/','Title','HOMEPAGE_BLOCK','','text')
    assert 'item_metadata' not in p.payload() and 'content_kind' not in p.payload()

def test_source_identities_distinct():
    from competitor_intel.execution import SOURCES
    assert 'jettools_ru' in SOURCES and 'jet_online' in SOURCES and 'rustan' in SOURCES and len(SOURCES)==11

def test_capture_time_and_raw_reference_are_not_semantic_version_changes():
    html='<h1>Новинки</h1><div class="component-item-product"><a class="title" href="/catalog/new/a/">JET Станок</a><div class="component-catalog-price"><div>1000 ₽</div></div></div>'
    first=cap('https://www.jettools.ru/catalog/new/',html);second=cap(first.url,html);second.fetched_at=datetime(2026,10,14,tzinfo=timezone.utc);second.raw_ref='another-file.html'
    a=extract('jettools_ru',first,'NOVELTY_INDEX');b=extract('jettools_ru',second,'NOVELTY_INDEX')
    assert a.content_hash()==b.content_hash()
    assert a.item_metadata!=b.item_metadata  # Original capture provenance is retained separately.

def test_canonical_link_cannot_collapse_active_campaign_pagination():
    html='<link rel="canonical" href="https://belmash.ru/products/top-offers/active/"><h1>Акция</h1>'
    page=extract('belmash',cap('https://belmash.ru/products/top-offers/active/?PAGEN_1=2',html),'PROMOTION')
    assert page.canonical_url.endswith('?PAGEN_1=2')

def test_later_snapshot_cannot_reuse_initial_audit_as_current_live_evidence():
    from competitor_intel.five_source_runner import initial_audit_cache
    audit={'captures':[{'url':'https://rustan.ru/','observed_at':'2026-10-13T10:00:00Z'}]}
    assert initial_audit_cache(audit,False)
    assert initial_audit_cache(audit,True)=={}
    assert initial_audit_cache({},False)=={}

def test_active_campaign_pager_cannot_expand_into_parent_catalogue():
    from competitor_intel.five_source_extract import discover
    html='<div class="pagination"><a href="/products/top-offers/?PAGEN_1=2">Общие предложения</a><a href="/products/top-offers/active/?PAGEN_1=2">2</a></div>'
    urls=[r['url'] for r in discover('belmash',cap('https://belmash.ru/products/top-offers/active/',html))]
    assert urls==['https://belmash.ru/products/top-offers/active/?PAGEN_1=2']
