"""Synthetic saved HTML/DOM only; never an operator's real source capture."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from competitor_intel.operator_capture import prepare_capture,sanitize_html,checked_original_url,checked_time
from competitor_intel.youmet_adapter import discover
from competitor_intel.business_html import render_html
from competitor_intel.business_report import build_facts

AT='2026-10-09T12:00:00+03:00'
CARD='''<div class="catalog_item"><div class="item-title"><a href="/shop/lathe-a/"><span>Токарный станок A</span></a></div><meta itemprop="brand" content="BRAND"><meta itemprop="model" content="A"><meta itemprop="category" content="Токарные станки"><div class="price" data-currency="RUB"><span class="price_value">80 000</span><span class="price_currency"> ₽</span></div><div class="price discount"><span class="price_value">100 000</span></div><div class="sale_block">-20%</div><div class="item-stock"><span class="value">В наличии</span></div></div>'''
def html(kind='PROMOTION'):
    return '<html><h1>Тестовая страница — FIXTURE</h1><main><h1>Скидки на станки — FIXTURE</h1><time datetime="2026-10-01">1 октября 2026</time><div class="detail-text">Скидка и подарок. С 1 по 31 октября 2026.</div>'+('' if kind in ('NEWS','BLOG') else CARD)+'</main></html>'

def prep(tmp_path,body=None,url='https://youmet.ru/akcia/fixture/',kind='AUTO',at=AT):
    path=tmp_path/'saved.html';path.write_text(body or html(),encoding='utf-8')
    return prepare_capture(path,url,at,kind)

def test_saved_html_and_outerhtml_use_same_normalized_pipeline(tmp_path):
    a=prep(tmp_path)[1];b=prep(tmp_path,html().replace('<html>','').replace('</html>',''))[1]
    assert a.source=='youmet' and a.page_type=='PROMOTION'
    assert a.items[0].new_price==b.items[0].new_price==Decimal('80000')
    assert a.items[0].old_price==Decimal('100000') and a.items[0].discount_percent==Decimal('20.00')
    assert a.items[0].brand=='BRAND' and a.items[0].model=='A'
    assert a.items[0].availability=='В наличии' and a.items[0].price_type=='SALE'

def test_duplicate_payload_does_not_change_with_import_time(tmp_path):
    a=prep(tmp_path)[1];b=prep(tmp_path,at='2026-10-10T12:00:00+03:00')[1]
    assert a.content_hash()==b.content_hash()
    c=prep(tmp_path,html().replace('80 000','70 000'))[1]
    assert c.content_hash()!=a.content_hash()

def test_public_navigation_actual_urls_no_mirrors_or_guesses(tmp_path):
    body='<h1>Юмет — FIXTURE</h1><a href="/akcia/">Акции</a><a href="/about/news/">Новости</a><a href="/blog/">Блог</a><a href="/offers/current/">Специальные предложения</a><a href="https://perm.youmet.ru/akcia/">Акции Пермь</a>'
    _,p,meta,links=prep(tmp_path,body,'https://youmet.ru/')
    assert {l['kind'] for l in links}=={'PROMOTIONS','NEWS','BLOG','SPECIAL_OFFERS'}
    assert all(l['status']=='DISCOVERED_NOT_FETCHED' and 'perm.' not in l['url'] for l in links)
    assert p.page_type=='NAVIGATION_INDEX' and p.inclusion_reason
    assert meta['capture_method']=='OPERATOR_BROWSER_CAPTURE' and meta['fetch_status']=='OPERATOR_CAPTURE'

@pytest.mark.parametrize('url,kind',[('https://youmet.ru/about/news/fixture/','NEWS'),('https://youmet.ru/blog/fixture/','BLOG')])
def test_news_blog_own_date_and_separate_type(tmp_path,url,kind):
    _,p,_,_=prep(tmp_path,html(kind),url)
    assert p.page_type==kind and str(p.published_at)=='2026-10-01'
    assert p.items==[] and p.inclusion_reason=='PUBLICATION'

def test_promotion_dates_mechanics_and_price_roles(tmp_path):
    _,p,_,_=prep(tmp_path)
    assert str(p.valid_from)=='2026-10-01' and str(p.valid_to)=='2026-10-31'
    assert 'Ценовое предложение' in p.commercial_mechanics
    _,p,_,_=prep(tmp_path,html().replace('80 000','8 000 ₽/мес'))
    assert p.items[0].price_type=='INSTALLMENT_MONTHLY' and p.items[0].new_price is None
    _,p,_,_=prep(tmp_path,html().replace('80 000','Персональная цена 80 000'))
    assert p.items[0].price_type=='PERSONAL'

def test_secrets_and_save_page_resources_not_persisted(tmp_path):
    body=html()+'''<script>document.cookie="COOKIE_SENTINEL"; localStorage.access_token="TOKEN_SENTINEL";</script><form><input type="password" value="PASSWORD_SENTINEL"></form><!--Authorization: Bearer COMMENT_SENTINEL--><div data-session-token="ATTR_SENTINEL">Safe public text</div><div>Authorization: Bearer TEXT_SENTINEL</div><a href="/blog/?access_token=URL_SENTINEL">Блог</a><img src="saved_files/private.jpg"><meta http-equiv="Set-Cookie" content="META_SENTINEL">'''
    cap,_,meta,links=prep(tmp_path,body)
    blob=cap.html+json.dumps(meta)+json.dumps(links)
    for secret in ('COOKIE_SENTINEL','TOKEN_SENTINEL','PASSWORD_SENTINEL','COMMENT_SENTINEL','ATTR_SENTINEL','TEXT_SENTINEL','URL_SENTINEL','META_SENTINEL','saved_files/private.jpg'):assert secret not in blob
    assert 'Safe public text' in cap.html and '<script' not in cap.html and '<form' not in cap.html
    assert 'COOKIE_SENTINEL' in (tmp_path/'saved.html').read_text(encoding='utf-8') # input remains read-only

@pytest.mark.parametrize('url',['https://perm.youmet.ru/akcia/','https://youmet.ru/personal/','https://youmet.ru/api/private/','https://youmet.ru/?sessid=SECRET'])
def test_unsafe_original_url_refused(url):
    with pytest.raises(ValueError):checked_original_url(url)

def test_no_mtime_or_naive_time_fallback():
    with pytest.raises(ValueError):checked_time('2026-10-01T12:00:00')
    with pytest.raises(ValueError):checked_time(None)

def test_challenge_and_authenticated_pages_refused(tmp_path):
    with pytest.raises(ValueError,match='Challenge'):prep(tmp_path,'<title>Captcha</title><h1>Проверка</h1>')
    with pytest.raises(ValueError,match='Authenticated'):prep(tmp_path,html()+'<a href="/?logout=yes">Выйти</a>')

def test_first_capture_initial_baseline_provenance_visible(tmp_path):
    cap,p,meta,_=prep(tmp_path)
    snapshot={'source':'youmet','entity':p.canonical_url,'version':p.content_hash(),'extractor':p.extractor_version,'observed_at':AT,'page_type':p.page_type,'title':p.title,'published_at':str(p.published_at),'valid_from':str(p.valid_from),'valid_to':str(p.valid_to),'text':p.normalized_text,'items':p.payload()['items'],'item_scope_complete':False,'source_discovery_complete':False}
    d={'sources':[{'code':'youmet','name':'YouMet','region':'Москва','capabilities':['PROMOTIONS','NEWS','BLOG'],'capture_methods':['OPERATOR_BROWSER_CAPTURE'],'access_gaps':[{'url':'https://youmet.ru/','fetch_status':'HTTP_BLOCKED'}]}],'current':[snapshot],'history':[snapshot],'analysis':[]}
    facts=build_facts(d,'2026-09-01','2026-10-09')
    assert {e['event_type'] for e in facts['events']}=={'BASELINE_OBSERVATION'}
    assert facts['summary']['new_products'] is None and facts['summary']['price_decreases'] is None
    rendered=render_html(json.loads(json.dumps(facts,default=str)))
    assert 'OPERATOR_BROWSER_CAPTURE' in rendered and 'Прямой автоматический доступ остаётся неполным' in rendered

def test_homepage_commercial_links_preserved_without_fetching_children(tmp_path):
    body='<main><h1>Юмет FIXTURE</h1><div class="item inner_wrap"><div class="info"><div class="date">1 июля 2026</div><a class="name" href="/akcia/123/">Промокод на станки</a></div></div></main>'
    _,p,_,links=prep(tmp_path,body,'https://youmet.ru/')
    assert p.homepage_blocks[0]['title']=='Промокод на станки'
    assert p.payload()['homepage_blocks'][0]['detail_status']=='DISCOVERED_NOT_FETCHED'
    assert links[0]['url']=='https://youmet.ru/akcia/123/'

@pytest.mark.parametrize('body',['{"log":{"entries":[]}}','<h1>Подтвердите, что вы человек</h1>'])
def test_har_and_titleless_challenge_are_not_accepted(tmp_path,body):
    with pytest.raises(ValueError):prep(tmp_path,body,'https://youmet.ru/')

def test_cli_import_dispatch_and_unified_report_without_crawl(monkeypatch,tmp_path,capsys):
    import sys
    import competitor_intel.__main__ as cli
    import competitor_intel.operator_capture as importer
    import competitor_intel.business_report as reporting
    calls=[]
    class FakeStore:
        def __init__(self,*a):pass
        def close(self):pass
    def import_spy(*args):
        calls.append(('import',args));return {'status':'YOUMET_PUBLIC_BROWSER_BASELINE_VERIFIED'}
    def report_spy(*args):calls.append(('report',args));return {'sources':6}
    def forbidden(*args,**kwargs):raise AssertionError('HTTP/crawl/AI path must not run')
    monkeypatch.setattr(cli,'Store',FakeStore);monkeypatch.setattr(cli,'crawl',forbidden);monkeypatch.setattr(cli,'analyze_pending',forbidden)
    monkeypatch.setattr(importer,'import_browser_capture',import_spy);monkeypatch.setattr(reporting,'preview_from_store',report_spy)
    monkeypatch.setattr(sys,'argv',['competitor_intel','import-browser-capture','--source','youmet','--url','https://youmet.ru/','--file',str(tmp_path/'saved.html'),'--capture-time',AT,'--report-output',str(tmp_path/'preview')])
    cli.main()
    assert [c[0] for c in calls]==['import','report']
    assert calls[0][1][3]==AT and calls[1][1][3]==tmp_path/'preview'

@pytest.mark.parametrize('encoding',['utf-8','utf-16','cp1251'])
def test_copy_outerhtml_file_encoding_precedes_original_charset(tmp_path,encoding):
    body='<meta charset="windows-1251">'+html().replace('₽','&#8381;')
    path=tmp_path/'saved.html';path.write_bytes(body.encode(encoding))
    _,p,_,_=prepare_capture(path,'https://youmet.ru/akcia/fixture/',AT)
    assert p.items[0].product_name=='Токарный станок A'

def test_output_cannot_overwrite_input_capture(tmp_path):
    from competitor_intel.operator_capture import import_browser_capture
    cap,_,_,_=prep(tmp_path)
    source=tmp_path/(cap.raw_hash+'.sanitized.html');source.write_bytes(html().encode('utf-8'));before=source.read_bytes()
    with pytest.raises(ValueError,match='overwrite'):import_browser_capture(object(),source,'https://youmet.ru/akcia/fixture/',AT,tmp_path)
    assert source.read_bytes()==before

def test_region_from_observed_current_city_selector(tmp_path):
    body='<div class="region_wrapper"><div class="js_city_chooser"><span>Москва</span></div></div>'+html()
    _,p,meta,_=prep(tmp_path,body)
    assert p.items[0].region=='Москва' and meta['source_region']=='Москва'

def test_hidden_secret_state_containers_removed(tmp_path):
    cap,_,_,_=prep(tmp_path,html()+'<div class="session_token"><span>STATE_SECRET</span></div><div id="cookie_state">COOKIE_STATE_SECRET</div>')
    assert 'STATE_SECRET' not in cap.html and 'COOKIE_STATE_SECRET' not in cap.html

def test_nonsecret_variant_query_preserved_without_identity_collapse(tmp_path):
    cap,p,_,_=prep(tmp_path,html().replace('/shop/lathe-a/','/shop/lathe-a/?offer_id=42'))
    assert p.items[0].product_url=='https://youmet.ru/shop/lathe-a/?offer_id=42'
