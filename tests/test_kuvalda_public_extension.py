from datetime import date
import pytest
import requests
from competitor_intel.kuvalda_allowed import KuvaldaRobots,KuvaldaHttp,BASE,navigation,parse_allowed,blocks
from competitor_intel.transport import USER_AGENT,SourceBlocked
from competitor_intel.parser import parse

RAW='''User-agent: Yandex
Disallow: /
User-Agent: *
Disallow: /promo/
Disallow: /news/
Disallow: *?*
Disallow: /data/
Allow: /data/sitemap_new/
Allow: *.js?v=*
'''
SINCE=date(2026,9,1); TODAY=date(2026,10,9)

@pytest.mark.parametrize('path,allowed',[('/',True),('/sale/',True),('/sale/2037/',True),('/promo/',False),('/promo/item-1/',False),('/%70romo/item-1/',False),('/news/',False),('/?group=212&isSale=',False),('/data/sitemap_new/nnov.xml',True),('/data/other.xml',False),('/themes/a.js?v=123',True)])
def test_robots_informational_wildcards(path,allowed):
    assert KuvaldaRobots(RAW).decision(BASE.rstrip('/')+path)[0] is allowed

def test_public_fetch_despite_recorded_robots(tmp_path):
    h=KuvaldaHttp(tmp_path,sleeper=lambda _:None)
    h.robots['nnov.kuvalda.ru']=KuvaldaRobots(RAW)
    calls=[]
    r=requests.Response(); r.status_code=200; r._content=b'<title>Public promo</title>'; r.url=BASE+'promo/'
    h.session.get=lambda *a,**k:(calls.append(a[0]) or r)
    assert h.get(BASE+'promo/').html=='<title>Public promo</title>'
    assert calls==[BASE+'promo/']

@pytest.mark.parametrize('path',['auth/','admin/','_backend/','api/private/','account/','cart/'])
def test_private_paths_refused(path,tmp_path):
    assert not KuvaldaHttp(tmp_path).permitted(BASE+path)

@pytest.mark.parametrize('status,html,expected',[(403,'<title>Forbidden</title>','HTTP_BLOCKED'),(200,'<title>Captcha</title>','CAPTCHA_BLOCKED')])
def test_actual_protection_still_stops(tmp_path,status,html,expected):
    h=KuvaldaHttp(tmp_path,sleeper=lambda _:None); h.robots['nnov.kuvalda.ru']=KuvaldaRobots(RAW)
    r=requests.Response(); r.status_code=status; r._content=html.encode(); r.url=BASE+'promo/'
    h.session.get=lambda *a,**k:r
    with pytest.raises(SourceBlocked): h.get(BASE+'promo/')
    assert h.events[-1]['access_failure']==expected
    with pytest.raises(SourceBlocked): h.get(BASE+'news/')

def test_sale_navigation_priority_and_unrelated_preserved():
    html='<a href="/sale/2037/">Станки</a><a href="/sale/1948/">Сварочное оборудование</a><a href="/sale/1915/">Воздушные компрессоры</a><a href="/sale/6000/">Товары для отдыха</a>'
    n=navigation(BASE+'sale/',html)
    assert len(n)==4 and len([e for e in n if e['selected']])==3

def test_exact_campaign_dom_dates_special_price():
    html='''<h1>Гарантированный подарок при покупке инструмента DongCheng</h1><div class="page-header__status">Акция действует до 31.10.26</div><div class="section container"><p>Только с 5 августа по 31 октября 2026 года, купите инструмент и получите подарок.</p></div><div class="snippet"><a class="snippet__title" href="/catalog/6514/product-238085/">Шлифмашина DongCheng</a><div class="snippet__price"><div class="snippet-price__value">25 990 ₽</div></div><div data-label="Персональная цена" data-tooltip="для авторизованных пользователей"></div></div>'''
    p=parse_allowed(BASE+'promo/item-3028/',html,SINCE,TODAY)
    assert p.title.startswith('Гарантированный подарок')
    assert (p.valid_from,p.valid_to)==(date(2026,8,5),date(2026,10,31))
    assert p.published_at is None and p.inclusion_reason=='CAMPAIGN_OVERLAP'
    assert p.items[0].new_price==25990 and p.items[0].old_price is None and p.items[0].price_type=='AUTH_REQUIRED'

def test_listing_end_date_is_source_proof_not_observation_year():
    html='<h1>Акция</h1><div class="page-header__status">Акция действует до 30.09.26</div><div class="section container"><p>Получите подарок</p></div>'
    p=parse_allowed(BASE+'promo/item-1/',html,SINCE,TODAY,{'valid_to':'2026-09-30','listing_url':BASE+'promo/','raw_capture_ref':'actual-source.html'})
    assert p.valid_to==date(2026,9,30) and p.published_at is None

def test_metal_parser_hash_unchanged_by_scoped_version_override():
    html='<h1>MM</h1>'
    mm=parse('metalmaster','https://metalmaster.ru/top_action/',html,SINCE,TODAY)
    assert not hasattr(mm,'extractor_version')
    kuv=parse_allowed(BASE+'sale/2037/',html,SINCE,TODAY)
    assert kuv.extractor_version=='kuvalda-allowed-1.1'

def test_index_only_is_not_price_source():
    # No index fetch/extraction adapter exists; navigation stores metadata only.
    n=navigation(BASE,'<a href="https://index.example/promo/">Sale 100 ₽</a>')
    assert n==[]

def test_auth_marker_without_amount_remains_auth_not_unknown():
    html='<h1>Сварочное оборудование</h1><div class="alt-snippet"><a class="alt-snippet__title" href="/catalog/1/product-1/">Электроды</a><div data-label="Персональная цена" data-tooltip="для авторизованных пользователей"></div><div class="alt-snippet__price"></div></div>'
    p=parse_allowed(BASE+'sale/1948/',html,SINCE,TODAY)
    assert p.items[0].price_type=='AUTH_REQUIRED'
    assert p.items[0].new_price is None and p.items[0].old_price is None

def test_cli_kuvalda_uses_corrected_public_policy(monkeypatch,capsys):
    import sys
    from competitor_intel import __main__ as cli
    from competitor_intel import kuvalda_public_runner
    calls=[]
    class FakeStore:
        def close(self): calls.append('closed')
    monkeypatch.setattr(cli,'Store',lambda dsn:FakeStore())
    monkeypatch.setattr(kuvalda_public_runner,'crawl_public',lambda *a,**k:(calls.append('public_policy') or {'status':'test'}))
    monkeypatch.setattr(cli,'crawl',lambda *a,**k: (_ for _ in ()).throw(AssertionError('legacy robots-hard-block runner')))
    monkeypatch.setattr(sys,'argv',['competitor_intel','crawl','--source','kuvalda_nnov'])
    cli.main()
    assert calls==['public_policy','closed']

def test_optional_sitemap_warning_is_not_source_blocker():
    from competitor_intel.kuvalda_public_runner import public_run_status
    result={'source_map':[{'url':BASE+'sitemap.xml','kind':'SITEMAP'}],'errors':[{'url':BASE+'sitemap.xml'}],'queue_remaining':[]}
    assert public_run_status(result)=='DONE_WITH_WARNINGS'
    assert public_run_status({**result,'errors':[{'url':BASE+'promo/'}]})=='BLOCKED'
    assert public_run_status({**result,'queue_remaining':[{'url':BASE+'sale/'}]})=='BLOCKED'
