from datetime import datetime,timezone
from competitor_intel.transport import Capture
from competitor_intel.four_source_extract import extract
from competitor_intel.source_transport import decode_public_html,SourceTransport

def capture(html,url='https://jet-online.ru/special_offers/'):
    return Capture(url,url,html,'fixture','hash',datetime(2026,10,12,tzinfo=timezone.utc),'FIXTURE')

def test_declared_legacy_charset():
    assert 'Новости' in decode_public_html('<meta charset="windows-1251">Новости'.encode('cp1251'))

def test_jet_dom_prices_do_not_use_analytics_or_monthly_payment():
    html='''<h1>Акции</h1><a class="product" data-product='{"brand":"JET","price":"99999","category":"Станки"}' href="/product/a/"><div class="pn">Станок A</div><div class="article">A</div><div class="product-price"><span class="price">10 000 ₽/мес</span><span class="old-price">20 000 ₽</span></div></a>'''
    p=extract('jet_online',capture(html),'SALE_PAGE')
    assert p.items[0].price_type=='INSTALLMENT_MONTHLY'
    assert p.items[0].new_price is None and p.items[0].old_price is None

def test_kami_rub_currency_and_exact_model_url():
    html='''<h1>Металлообрабатывающие станки по акции</h1><div class="catalogTable__row_body"><div class="catalogTable__cell_model"><a href="/catalog/a/execution/">ABC</a></div><div class="catalogTable__cell_price"><del data-currency="rub">200 000 ₽</del><span data-currency="rub">150 000 ₽</span><span data-currency="usd">2 000 $</span><i data-title="В наличии"></i></div></div>'''
    p=extract('stanki_ru',capture(html,'https://www.stanki.ru/sale/metal/'),'SALE_PAGE')
    assert str(p.items[0].new_price)=='150000'
    assert p.items[0].product_url=='https://www.stanki.ru/catalog/a/execution/'

def test_titanium_base_href_and_no_catalog_promotion_on_home():
    html='''<base href="https://titaniumgroup.ru/"><h1>Главная</h1><div class="shk-item product"><a class="title" href="catalog/a.html">Станок A</a><div class="info"><div class="price">100 ₽</div></div></div>'''
    p=extract('titaniumgroup',capture(html,'https://titaniumgroup.ru/'),'HOMEPAGE_BLOCK')
    assert p.page_type=='NAVIGATION_INDEX' and not p.items
    p=extract('titaniumgroup',capture(html,'https://titaniumgroup.ru/actions/'),'SALE_PAGE')
    assert p.items[0].product_url=='https://titaniumgroup.ru/catalog/a.html'

def test_blog_separate_and_old_archive_excluded():
    p=extract('titaniumgroup',capture('<h1>Статья</h1><div class="date">25 марта 2026</div>','https://titaniumgroup.ru/articles/a.html'),'BLOG')
    assert p.page_type=='BLOG' and p.inclusion_reason is None

def test_transport_no_region_or_private_endpoints(tmp_path):
    h=SourceTransport({'hosts':['youmet.ru'],'base_url':'https://youmet.ru/'},tmp_path,sleeper=lambda _:None)
    assert h.permitted('https://youmet.ru/actions/')
    assert not h.permitted('https://moscow.youmet.ru/actions/')
    assert not h.permitted('https://youmet.ru/api/private/')

def test_own_publication_date_precedes_related_dates_and_blog_prices_absent():
    html='''<div class="page-content"><h1>Статья</h1><div class="date-publication">24 сентября 2026 г.</div><div class="date">15 сентября 2026</div><div class="shk-item product"><a class="title" href="/catalog/a.html">Станок</a><div class="info"><div class="price">100 ₽</div></div></div></div>'''
    p=extract('titaniumgroup',capture(html,'https://titaniumgroup.ru/articles/a.html'),'BLOG')
    assert str(p.published_at)=='2026-09-24' and p.items==[]

def test_jet_broken_analytics_and_call_for_price_are_noncommercial_null():
    html='''<h1>Акции</h1><a class="product" data-product="{broken" href="/product/a/"><div class="pn">Станок A</div><div class="product-price"><span class="price">Звоните!</span></div></a>'''
    p=extract('jet_online',capture(html),'SALE_PAGE')
    assert p.items[0].new_price is None and p.items[0].price_type=='UNKNOWN' and not p.items[0].warnings

def test_kami_only_three_refresh_reasons():
    from competitor_intel.historical_seed import plan_refresh
    plan=plan_refresh([],campaign_urls=['https://www.stanki.ru/catalog/a/'],max_products=1)
    assert plan['selected'][0]['reasons']==['CURRENT_CAMPAIGN_MEMBER']

def test_seed_cache_fails_on_changed_prepared_bytes(tmp_path):
    import json
    from competitor_intel.seed_cache import verified_seed_bundle,SEED_LOGICAL_SHA
    import pytest
    (tmp_path/'HISTORICAL_SEEDS.jsonl').write_bytes(b'{"tampered":true}\r\n')
    (tmp_path/'SEED_RESULT.json').write_text(json.dumps({'output_sha256':SEED_LOGICAL_SHA}))
    with pytest.raises(ValueError,match='integrity'):verified_seed_bundle(tmp_path)

def test_campaign_deadline_not_confused_with_machine_dimensions():
    html='''<main><h1>Выгодный комплект</h1><div class="news-detail__date">24 сентября 2026</div><section class="news-detail__section">Труба до 3 метров. Со скидкой до 15 октября. <a href="/catalog/a/">Ironmac A</a></section></main>'''
    p=extract('stanki_ru',capture(html,'https://www.stanki.ru/about/news/detail/a/'),'NEWS')
    assert str(p.valid_to)=='2026-10-15' and p.campaign_evidence['current_campaign_in_news']
    assert p.items[0].product_url=='https://www.stanki.ru/catalog/a/' and p.items[0].new_price is None
