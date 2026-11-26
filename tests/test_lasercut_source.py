"""lasercut_ru (12th source) integration tests: discovery, price semantics, date parsing,
pagination bound, canonical identity, dedup, scope safety, provenance, source isolation."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
import json

from competitor_intel.model import SOURCES
from competitor_intel.parser import parse, discover, content_type

SINCE = date(2026, 9, 1)
TODAY = date(2026, 10, 9)
LC = 'lasercut_ru'

PROMO_LIST = '''<html><head><base href="https://lasercut.ru/"><link rel="canonical" href="https://lasercut.ru/vse-aktsii/"></head><body><main><h1>Акции</h1>
<ul>
<li class="catalog-media__banner"><a class="catalog-media__banner-link" href="vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/"><img class="catalog-media__banner-img" src="/assets/banner1.webp" alt="Скорость в доставке"></a></li>
<li class="catalog-media__banner"><a class="catalog-media__banner-link" href="https://lasercut.ru/vse-aktsii/stanki-wattsan-dlya-uchebnyh-zavedeniy-pod-klyuch/?utm_source=x"><img class="catalog-media__banner-img" src="/assets/banner2.webp" alt="Станки Wattsan"></a></li>
</ul>
<a href="/vse-aktsii/?page=2">Следующая страница</a>
</main></body></html>'''

PROMO_DETAIL = '''<html><head><link rel="canonical" href="https://lasercut.ru/vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/"></head><body><main>
<h1>Скорость в доставке — мощность в работе</h1>
<div class="content__body"><p>Доставим лазерный станок Wattsan за 5 дней.</p>
<a href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1616/">Лазерный станок по металлу Wattsan 1616</a></div>
</main></body></html>'''

NEWS_LIST = '''<html><head><base href="https://lasercut.ru/"><link rel="canonical" href="https://lasercut.ru/novosti/"></head><body><main><h1>Новости</h1>
<article class="rubricator-item__wrapper">
<a class="rubricator-item__img-wrapper" href="novosti/chpu-kontroller-novogo-pokoleniya/"><img class="rubricator-item__img" alt="Ruida"></a>
<time class="rubricator-item__date" itemprop="datePublished" datetime="2026-09-15">15 сентября 2026</time>
<a class="rubricator-item__link" href="novosti/chpu-kontroller-novogo-pokoleniya/"><h3 class="rubricator-item__title">ЧПУ контроллер нового поколения Ruida</h3></a>
</article>
<article class="rubricator-item__wrapper">
<a class="rubricator-item__img-wrapper" href="novosti/staraya-novost/"><img class="rubricator-item__img" alt="старая"></a>
<time class="rubricator-item__date" itemprop="datePublished" datetime="2026-06-01">1 июня 2026</time>
<a class="rubricator-item__link" href="novosti/staraya-novost/"><h3 class="rubricator-item__title">Старая новость</h3></a>
</article>
<a href="/novosti/?page=2">Следующая</a>
<a href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/">Станок Wattsan 1313A</a>
</main></body></html>'''
# __PART2__
NEWS_DETAIL = '''<html><head><link rel="canonical" href="https://lasercut.ru/novosti/chpu-kontroller-novogo-pokoleniya/"></head><body><main>
<h1>ЧПУ контроллер нового поколения: новинка от бренда Ruida</h1>
<time class="meta meta--datetime" datetime="2026-09-15T14:09:00+03:00">15 сентября 2026</time>
<div class="content__body"><p>Компания LASERCUT сообщает о старте продаж.</p>
<a href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1616/">Лазерный станок по металлу Wattsan 1616</a>
<a href="/katalog/komplektuyushhie/linzy/">Линза для лазерного станка</a></div>
</main></body></html>'''

BLOG_LIST = '''<html><head><base href="https://lasercut.ru/"><link rel="canonical" href="https://lasercut.ru/blog/"></head><body><main><h1>Блог</h1>
<article class="rubricator-item__wrapper">
<a class="rubricator-item__img-wrapper" href="blog/kak-vybrat-chpu-stanok/"><img class="rubricator-item__img" alt="чпу"></a>
<time class="rubricator-item__date" itemprop="datePublished" datetime="2026-09-20">20 сентября 2026</time>
<a class="rubricator-item__link" href="blog/kak-vybrat-chpu-stanok/"><h3 class="rubricator-item__title">Как выбрать ЧПУ станок</h3></a>
</article>
<a href="/blog/stati-o-lazernom-oborudovanii/">Статьи о лазерном оборудовании</a>
<a href="/blog/prochee/">Прочее</a>
<a href="/blog/programmnoe-obespechenie/">Программное обеспечение</a>
<a href="/blog/?page=2">Следующая</a>
</main></body></html>'''

BLOG_ARTICLE = '''<html><head><link rel="canonical" href="https://lasercut.ru/blog/kak-vybrat-chpu-stanok/"></head><body><main>
<h1>Как выбрать ЧПУ станок для начинающих</h1>
<time class="meta meta--datetime" datetime="2026-09-20T10:00:00+03:00">20 сентября 2026</time>
<div class="content__body"><p>Обзор.</p></div>
</main></body></html>'''

HOMEPAGE = '''<html><head><link rel="canonical" href="https://lasercut.ru/"></head><body><main><h1>LASERCUT — лазерное оборудование</h1>
<ul class="card__list">
<li class="card__item card-item"><div class="card-item__wrapper">
<a class="card-item__link" href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/"><h3 class="card-item__title"><span class="visually-hidden">Станок по металлу Wattsan</span><span class="card-item__title-big" itemprop="model">1313A</span><span class="card-item__title-usually">для резки листа</span></h3></a>
<p class="card-item__presence">В наличии</p>
<s class="card-item__price-old nope">3 380 000 ₽</s><p class="card-item__price">2 921 000 ₽</p></div></li>
<li class="card__item card-item"><div class="card-item__wrapper">
<a class="card-item__link" href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1616/"><h3 class="card-item__title"><span class="visually-hidden">Станок по металлу Wattsan</span><span class="card-item__title-big" itemprop="model">1616</span></h3></a>
<p class="card-item__presence">Под заказ</p><p class="card-item__price">4 500 000 ₽</p></div></li>
</ul>
<nav class="mainnav"><a href="/vse-aktsii/">Акции</a> <a href="/novosti/">Новости</a> <a href="/blog/">Блог</a>
<a href="/katalog/lazernyie-stanki-po-metallu/">Лазерные станки по металлу</a>
<a href="/katalog/komplektuyushhie/">Комплектующие</a></nav>
</main></body></html>'''
# __PART3__
CATEGORY = '''<html><head><link rel="canonical" href="https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/"></head><body><main>
<h1>Лазерные станки по металлу</h1>
<ul class="card__list" id="mse2_results">
<li class="card__item card-item"><div class="card-item__wrapper" itemprop="item" itemscope itemtype="https://schema.org/Product">
<a class="card-item__link" href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/"><h3 class="card-item__title"><span class="visually-hidden">Станок по металлу Wattsan</span><span class="card-item__title-big" itemprop="model">1313A</span><span class="card-item__title-usually">для резки листа</span></h3></a>
<p class="card-item__presence">В наличии</p>
<s class="card-item__price-old nope">3 380 000 ₽</s>
<p class="card-item__price">2 921 000 ₽</p>
<span class="card-item__price-usually">Обычная цена 3 380 000 ₽</span>
</div></li>
<li class="card__item card-item"><div class="card-item__wrapper" itemprop="item" itemscope itemtype="https://schema.org/Product">
<a class="card-item__link" href="/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1616/"><h3 class="card-item__title"><span class="visually-hidden">Станок по металлу Wattsan</span><span class="card-item__title-big" itemprop="model">1616</span></h3></a>
<p class="card-item__presence">Под заказ</p>
<p class="card-item__price">4 500 000 ₽</p>
</div></li>
</ul>
<a href="/katalog/lazernyie-stanki-po-metallu/?page=2">Следующая</a>
</main></body></html>'''

CATEGORY_FROM_PRICE_LIVE = '''<html><head><link rel="canonical" href="https://lasercut.ru/katalog/lazernyie-stanki/"></head><body><main>
<h1>Лазерный станок, гравер</h1>
<ul class="card__list" id="mse2_results">
<li class="card__item card-item"><div class="card-item__wrapper" itemprop="item" itemscope itemtype="https://schema.org/Product">
<a class="card-item__link" href="/katalog/lazernyie-stanki/kopiya-lazernyy-stanok-wattsan-6090-pro/"><h3 class="card-item__title" id="title_16"><span class="visually-hidden">Лазерный станок Wattsan</span><span class="card-item__title-big" itemprop="model">1290</span><span class="card-item__title-usually">PRO</span></h3></a>
<p class="card-item__vendor" itemprop="brand"><span itemprop="name">Wattsan</span></p>
<div class="card-item__price-wrapper" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
<p class="card-item__price card-item__price-stock" data-price-version="v3">
<s class="card-item__price-old nope"> 873 000 ₽ </s><br/>
<span>от</span>
<span class="card-item__price-usually card-item__price-stock"> 799 000 ₽ </span>
</p>
<div class="card-item__price-btn-wrap"><a class="btn btn--black" href="/katalog/lazernyie-stanki/kopiya-lazernyy-stanok-wattsan-6090-pro/">Подробнее</a></div>
<meta content="RUB" itemprop="priceCurrency"/>
<meta content="https://schema.org/InStock" itemprop="availability"/>
</div>
</div></li>
<li class="card__item card-item"><div class="card-item__wrapper" itemprop="item" itemscope itemtype="https://schema.org/Product">
<a class="card-item__link" href="/katalog/stanki-dlya-proizvodstva-mebeli/obrabatyvayuschiy-tsentr-quick-sa-1313d-5a/"><h3 class="card-item__title"><span class="visually-hidden">Обрабатывающий центр</span><span class="card-item__title-big" itemprop="model">Quick</span></h3></a>
<div class="card-item__price-wrapper" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
<p class="card-item__price card-item__price-stock" data-price-version="v3">
<s class="card-item__price-old nope"> 16 177 000 ₽ </s><br>
<span class="card-item__price-usually card-item__price-stock"> 14 806 000 ₽ </span>
</p>
</div>
</div></li>
</ul>
</main></body></html>'''

ACCESSORIES = '''<html><head><link rel="canonical" href="https://lasercut.ru/katalog/komplektuyushhie/"></head><body><main>
<h1>Комплектующие</h1>
<ul class="card__list"><li class="card__item card-item"><div class="card-item__wrapper">
<a class="card-item__link" href="/katalog/komplektuyushhie/linza/"><h3 class="card-item__title"><span class="card-item__title-big">Линза ZnSe</span></h3></a>
<p class="card-item__presence">В наличии</p><p class="card-item__price">5 000 ₽</p></div></li></ul>
</main></body></html>'''

PRODUCT = '''<html><head><link rel="canonical" href="https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/"></head><body><main>
<h1>Лазерный станок по металлу Wattsan 1313A</h1>
<div class="offer__price">
<div class="tabs__panel" id="old"><span class="tabs__label">под заказ</span><s class="nope nope--stock">3 380 000 ₽</s><p class="price">2 921 000 ₽</p><span class="badge">Акция</span></div>
<div class="tabs__panel" id="stock"><span class="tabs__label">из наличия</span><p class="price">3 181 000 ₽</p></div>
</div>
<span itemprop="model">1313A</span>
</main></body></html>'''
# __PART4__
def test_source_registered():
    assert SOURCES['lasercut_ru']['base_url'] == 'https://lasercut.ru/'
    assert SOURCES['lasercut_ru']['region'] == 'Санкт-Петербург'
    assert SOURCES['lasercut_ru']['seeds'] == ['/', '/vse-aktsii/', '/novosti/', '/blog/']
    from competitor_intel.execution import SOURCES as EXEC_SOURCES
    assert 'lasercut_ru' in EXEC_SOURCES and len(EXEC_SOURCES) == 12
    caps = json.loads(Path('competitor_intel/source_capabilities.json').read_text(encoding='utf-8'))['sources']['lasercut_ru']
    assert {'PROMOTIONS', 'NEWS', 'BLOG', 'SALE_PRODUCTS', 'HOMEPAGE_BLOCKS', 'PRODUCT_PRICES', 'PRODUCT_DETAIL'} <= set(caps)
    assert 'CAMPAIGN_DATES' not in caps  # site publishes no campaign validity dates
    cfgs = json.loads(Path('competitor_intel/new_sources.json').read_text(encoding='utf-8'))
    assert 'lasercut.ru' in cfgs['lasercut_ru']['hosts']


def test_promotion_list_discovery_bounded():
    # Pagination is a robots-allowed query URL; the generic runner filters query
    # targets, exactly as for the other sources.
    found = [r['url'] for r in discover(LC, 'https://lasercut.ru/vse-aktsii/', PROMO_LIST, SINCE, TODAY)
             if not urlsplit(r['url']).query]
    assert found == ['https://lasercut.ru/vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/',
                     'https://lasercut.ru/vse-aktsii/stanki-wattsan-dlya-uchebnyh-zavedeniy-pod-klyuch/']


def test_promotion_detail_current_promo_no_fabricated_dates():
    page = parse(LC, 'https://lasercut.ru/vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/?utm_source=x', PROMO_DETAIL, SINCE, TODAY)
    assert page.page_type == 'PROMOTION'
    assert page.canonical_url == 'https://lasercut.ru/vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/'
    assert page.published_at is None and page.valid_from is None and page.valid_to is None
    assert page.inclusion_reason == 'CURRENT_PROMO'
    assert len(page.items) == 1
    item = page.items[0]
    assert item.old_price is None and item.new_price is None  # no fabricated prices
    assert item.campaign_name == page.title and item.priority == 'HIGH'
    assert item.region == 'Санкт-Петербург' and 'wattsan-1616' in item.product_url


def test_promotion_detail_explicit_end_date():
    html = PROMO_DETAIL.replace('</div>', '<p>Акция действует до 31 октября 2026</p></div>', 1)
    page = parse(LC, 'https://lasercut.ru/vse-aktsii/skorost-v-dostavke-moschnost-v-rabote/', html, SINCE, TODAY)
    assert page.valid_to == date(2026, 10, 31)
    assert page.inclusion_reason == 'END_DATE_OVERLAP_START_UNKNOWN'


def test_news_list_discovery_and_bounding():
    found = {r['url']: r for r in discover(LC, 'https://lasercut.ru/novosti/', NEWS_LIST, SINCE, TODAY)}
    assert 'https://lasercut.ru/novosti/chpu-kontroller-novogo-pokoleniya/' in found
    assert found['https://lasercut.ru/novosti/chpu-kontroller-novogo-pokoleniya/']['published_at'] == '2026-09-15'
    assert 'https://lasercut.ru/novosti/staraya-novost/' not in found  # out-of-window, no campaign overlap
    assert not any('wattsan-1313a' in u for u in found)  # product links never enqueued


def test_news_detail_publication_and_machine_links():
    page = parse(LC, 'https://lasercut.ru/novosti/chpu-kontroller-novogo-pokoleniya/', NEWS_DETAIL, SINCE, TODAY)
    assert page.page_type == 'NEWS' and page.published_at == date(2026, 9, 15)
    assert page.inclusion_reason == 'PUBLICATION'
    assert len(page.items) == 1 and 'Wattsan 1616' in page.items[0].product_name
    assert all('komplektuyushhie' not in (i.product_url or '') for i in page.items)  # lens link dropped


def test_blog_list_discovery_articles_only():
    found = {r['url']: r for r in discover(LC, 'https://lasercut.ru/blog/', BLOG_LIST, SINCE, TODAY)
             if not urlsplit(r['url']).query}
    assert list(found) == ['https://lasercut.ru/blog/kak-vybrat-chpu-stanok/']
    assert found['https://lasercut.ru/blog/kak-vybrat-chpu-stanok/']['published_at'] == '2026-09-20'


def test_blog_article_parse():
    page = parse(LC, 'https://lasercut.ru/blog/kak-vybrat-chpu-stanok/', BLOG_ARTICLE, SINCE, TODAY)
    assert page.page_type == 'NEWS' and page.published_at == date(2026, 9, 20)
    assert page.inclusion_reason == 'PUBLICATION'


def test_homepage_and_machine_category_discovery():
    urls = [r['url'] for r in discover(LC, 'https://lasercut.ru/', HOMEPAGE, SINCE, TODAY)]
    assert 'https://lasercut.ru/vse-aktsii/' in urls and 'https://lasercut.ru/novosti/' in urls and 'https://lasercut.ru/blog/' in urls
    assert 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/' in urls  # machine category root
    assert not any('komplektuyushhie' in u for u in urls)  # accessories never selected
    assert not any('wattsan-1313a' in u for u in urls)  # product details never enqueued


def test_homepage_promo_cards_only():
    page = parse(LC, 'https://lasercut.ru/', HOMEPAGE, SINCE, TODAY)
    assert page.page_type == 'HOMEPAGE_BLOCK' and page.inclusion_reason == 'CURRENT_PROMO'
    # Only the card evidencing an active promotion (shown old price) becomes an item.
    assert len(page.items) == 1 and page.items[0].old_price == Decimal('3380000')


def test_machine_category_cards_parse():
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/', CATEGORY, SINCE, TODAY)
    assert page.page_type == 'SALE_PAGE' and page.inclusion_reason == 'CURRENT_PROMO'
    assert len(page.items) == 2
    promo = next(i for i in page.items if i.old_price)
    assert promo.old_price == Decimal('3380000') and promo.new_price == Decimal('2921000')
    assert promo.discount_amount == Decimal('459000') and promo.price_type == 'SALE'
    assert promo.availability == 'В наличии' and promo.model == '1313A' and promo.currency == 'RUB'
    assert promo.priority == 'HIGH' and promo.region == 'Санкт-Петербург'
    assert 'wattsan-1313a' in promo.product_url and 'Обычная цена' in (promo.price_label_raw or '')
    assert promo.warnings == []
    regular = next(i for i in page.items if not i.old_price)
    assert regular.new_price == Decimal('4500000') and regular.price_type == 'PUBLIC'
    assert regular.availability == 'Под заказ'


def test_category_card_from_price_normalization_live_markup():
    # Exact run-50 live pattern: struck base + <span>от</span> + .card-item__price-usually.
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki/', CATEGORY_FROM_PRICE_LIVE, SINCE, TODAY)
    assert page.page_type == 'SALE_PAGE' and page.inclusion_reason == 'CURRENT_PROMO'
    from_price = next(i for i in page.items if i.price_type == 'FROM_PRICE')
    assert from_price.old_price == Decimal('873000')      # struck base preserved
    assert from_price.new_price == Decimal('799000')      # numeric "от X" extracted
    assert from_price.currency == 'RUB'
    # raw label preserved verbatim: base, "от" marker and both occurrences of X
    label = from_price.price_label_raw or ''
    assert 'от' in label and '873 000' in label and '799 000' in label
    assert from_price.warnings == []                       # no fabricated values
    assert from_price.brand == 'Wattsan' and from_price.model == '1290'
    assert 'wattsan-6090-pro' in from_price.product_url


def test_exact_price_card_stays_exact_no_from_regression():
    # The second card (struck base + in-stock price, no "от") must stay exact
    # SALE semantics with the from-price fix not touching it.
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki/', CATEGORY_FROM_PRICE_LIVE, SINCE, TODAY)
    plain = next(i for i in page.items if 'quick' in i.product_url)
    assert plain.old_price == Decimal('16177000') and plain.new_price is None
    assert plain.price_type == 'SALE' and plain.price_type != 'FROM_PRICE'
    # The classic exact-price card (struck old + plain current) stays SALE, not FROM_PRICE.
    classic = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/', CATEGORY, SINCE, TODAY)
    promo = next(i for i in classic.items if i.old_price)
    assert promo.price_type == 'SALE' and promo.new_price == Decimal('2921000')
    assert promo.discount_amount == Decimal('459000')


def test_from_price_never_exact_change_semantics():
    # FROM_PRICE stays review/non-comparable: excluded from PUBLIC_TYPES so
    # change detection can never emit exact PRICE_INCREASE/PRICE_DECREASE/DISCOUNT_CHANGED.
    from competitor_intel.change_detection import PUBLIC_TYPES
    assert 'FROM_PRICE' not in PUBLIC_TYPES
    from competitor_intel.business_report import discount as business_discount
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki/', CATEGORY_FROM_PRICE_LIVE, SINCE, TODAY)
    item = next(i for i in page.items if i.price_type == 'FROM_PRICE')
    assert business_discount({'old_price': item.old_price, 'new_price': item.new_price,
                              'price_type': item.price_type, 'currency': item.currency}) is None


def test_accessories_never_machine_facts():
    page = parse(LC, 'https://lasercut.ru/katalog/komplektuyushhie/', ACCESSORIES, SINCE, TODAY)
    assert page.page_type == 'OTHER' and page.items == [] and page.inclusion_reason is None


def test_product_detail_prices_and_contexts():
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/?utm_source=x', PRODUCT, SINCE, TODAY)
    assert page.page_type == 'PRODUCT_DETAIL'
    assert page.canonical_url == 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/lazernyy-stanok-po-metallu-wattsan-1313a/'
    assert page.inclusion_reason == 'CURRENT_PROMO'
    assert len(page.items) == 1
    item = page.items[0]
    assert item.old_price == Decimal('3380000') and item.new_price == Decimal('2921000')
    assert item.price_type == 'SALE' and item.availability == 'из наличия'
    assert 'под заказ' in item.price_label_raw and '3 181 000' in item.price_label_raw
    assert item.model == '1313A' and item.product_url == page.canonical_url


def test_reparse_stable_hash_and_price_change_version():
    url = 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/'
    a = parse(LC, url, CATEGORY, SINCE, TODAY)
    b = parse(LC, url, CATEGORY, SINCE, TODAY)
    assert a.content_hash() == b.content_hash()  # duplicate capture = no new version
    c = parse(LC, url, CATEGORY.replace('2 921 000', '2 800 000'), SINCE, TODAY)
    assert c.content_hash() != a.content_hash()  # real price change = new version


def test_partial_scope_not_claimed_complete():
    page = parse(LC, 'https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/', CATEGORY, SINCE, TODAY)
    payload = page.payload()
    # One category page is a bounded partial view; no completeness is claimed, so a
    # smaller later snapshot can never fabricate disappearance events.
    assert 'item_scope_complete' not in payload and 'source_discovery_complete' not in payload


def test_publication_evidence_from_discovery():
    found = [r for r in discover(LC, 'https://lasercut.ru/novosti/', NEWS_LIST, SINCE, TODAY) if r['published_at']]
    evidence = {'published_at': found[0]['published_at'], 'label': found[0]['label'][:80]}
    detail_without_time = NEWS_DETAIL.replace('<time class="meta meta--datetime" datetime="2026-09-15T14:09:00+03:00">15 сентября 2026</time>', '')
    page = parse(LC, found[0]['url'], detail_without_time, SINCE, TODAY, publication_evidence=evidence)
    assert page.published_at == date(2026, 9, 15) and page.inclusion_reason == 'PUBLICATION'
    assert page.publication_evidence == evidence


def test_source_isolation():
    # Existing sources keep their classification; lasercut markup is ignored by them.
    assert content_type('https://nnov.kuvalda.ru/news/x/') == 'NEWS'
    assert content_type('https://metalmaster.ru/top_action/') == 'CAMPAIGN'
    assert content_type('https://lasercut.ru/katalog/lazernyie-stanki-po-metallu/') == 'SALE_PAGE'
    assert content_type('https://lasercut.ru/katalog/komplektuyushhie/') == 'OTHER'
    assert content_type('https://lasercut.ru/blog/stati-o-lazernom-oborudovanii/') == 'OTHER'
    assert content_type('https://lasercut.ru/blog/kak-vybrat-chpu-stanok/') == 'NEWS'
    assert discover('kuvalda_nnov', 'https://nnov.kuvalda.ru/promo/', PROMO_LIST, SINCE, TODAY) == []
    assert discover('metalmaster', 'https://metalmaster.ru/news/', NEWS_LIST, SINCE, TODAY) == []



