"""MSK/SIB editorial page-role gate. Frozen articles fail closed; product cards stay."""
from pathlib import Path
import pytest
from tests.test_ltt_uss_adapters import load
from universal_supplier.http_capture import CaptureStatus
from universal_supplier.ltt_uss_capture import _card_gate

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13/FULL_RUN1_2026-10-14/EVIDENCE/ltt_msk'
ARTICLES = (
    ('1717', '9dd96264c02e194ba807a32ba9d86f8361c56ae616554480b256a4fd351889f8',
     'https://ltt-msk.ru/katalog/stati/myi-pilim,-pilim,-pilim!'),
    ('1721', 'de55d3bd294a71760ad53e70a483abb19730ec4c339c23372322700bd6a7b4b0',
     'https://ltt-msk.ru/katalog/stati/na-zavist-czaryu!'),
    ('1723', '68d50713978e00e623da9510b3697c7b72cb45a6199612fb197408604377d139',
     'https://ltt-msk.ru/katalog/stati/odin-chpu-v-pole-voin!'),
    ('1719', '6b5a83d321d872a5feea6c80a1a42242ef57add77f43ed5d38f3a30106a93ab4',
     'https://ltt-msk.ru/katalog/stati/potomki-giperboloida-inzhenera-garina'),
)


def page(supplier, title, crumb_name, crumb_href, body, *, product_id='9001', price='por',
         properties=(), leaf=None, url_slug='item'):
    host = 'https://ltt-msk.ru/' if supplier == 'ltt_msk' else 'https://lttsib.ru/'
    rows = ''.join(f'<tr><td>{key}</td><td>{value}</td></tr>' for key, value in properties)
    if price == 'both':
        price_html = ('<div class="product_price">100 руб.</div>'
                      '<div class="product_price_no_price">Цену уточняйте у менеджеров</div>')
    elif price == 'por':
        price_html = '<div class="product_price_no_price">Цену уточняйте у менеджеров</div>'
    else:
        price_html = '<div class="product_price">100 руб.</div>'
    leaf = leaf or title
    html = f'''<!DOCTYPE html><html><head><base href="{host}" /></head><body>
<ul><li><a href="novosti/">Новости</a></li><li><a href="katalog/stati/">статья</a></li></ul>
<div class="product" itemscope itemtype="http://schema.org/Product">
<h1 itemprop="name">{title}</h1>
<ul id="breadcrumbs" itemscope itemtype="http://schema.org/BreadcrumbList">
<li><span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="/"><span itemprop="name">Главная</span></a></span></li>
<li><span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="katalog/"><span itemprop="name">Каталог</span></a></span></li>
<li><span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><a itemprop="item" href="{crumb_href}"><span itemprop="name">{crumb_name}</span></a></span></li>
<li><span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem"><span itemprop="name">{leaf}</span></span></li>
</ul>
<form class="form-horizontal ms2_form"><input name="id" value="{product_id}"></form>
{price_html}
<span style="color:green">В наличии</span>
<table>{rows}</table>
<div class="product_content_block"><div class="content">{body}</div><div class="product_content_youtube"></div></div>
</div></body></html>'''
    return html, host + crumb_href + url_slug


@pytest.mark.parametrize('stable,sha,url', ARTICLES, ids=[row[0] for row in ARTICLES])
def test_frozen_msk_articles_fail_closed(stable, sha, url):
    html = (EVIDENCE / f'{sha}.html').read_text('utf-8')
    status, diagnostics = _card_gate('ltt_msk', html, url)
    assert status is CaptureStatus.NON_PRODUCT_EDITORIAL_PAGE
    assert diagnostics == ('EDITORIAL_NON_PRODUCT',)
    assert stable


def test_product_category_keeps_comparison_prose():
    html, url = page('ltt_msk', 'Фрезерный станок LTT-1000', 'Фрезерные станки', 'katalog/frezernye/',
                     'В данной статье мы сравниваем LTT-1000 и LTT-2000. Смотрите статью.')
    assert _card_gate('ltt_msk', html, url)[0] is CaptureStatus.SUCCESS


def test_editorial_section_with_property_table_stays_product():
    html, url = page('ltt_msk', 'Станок LTT-1000', 'Статьи', 'katalog/stati/',
                     'В данной статье сравниваем LTT-1000 и LTT-2000.',
                     properties=(('Модель', 'LTT-1000'), ('Мощность', '2.2 кВт')))
    assert _card_gate('ltt_msk', html, url)[0] is CaptureStatus.SUCCESS


def test_editorial_section_one_owned_model_without_article_voice_stays_product():
    html, url = page('ltt_msk', 'Станок LTT-1000', 'Статьи', 'katalog/stati/',
                     'Поставляется со склада. Смотрите статью в каталоге.')
    assert _card_gate('ltt_msk', html, url)[0] is CaptureStatus.SUCCESS


def test_editorial_voice_without_properties_fails_closed():
    html, url = page('ltt_msk', 'Один станок в поле', 'Статьи', 'katalog/stati/',
                     'В данной статье сравниваются два исполнения.')
    status, diagnostics = _card_gate('ltt_msk', html, url)
    assert status is CaptureStatus.NON_PRODUCT_EDITORIAL_PAGE
    assert diagnostics == ('EDITORIAL_NON_PRODUCT',)


def test_editorial_section_many_models_and_no_owned_title_model_fails_closed():
    html, url = page('ltt_msk', 'Потомки гиперболоида', 'Статьи', 'katalog/stati/',
                     'На складе есть LTT-Z3020 и LTT-Z6040B.')
    assert _card_gate('ltt_msk', html, url)[0] is CaptureStatus.NON_PRODUCT_EDITORIAL_PAGE


def test_same_shape_on_sib_is_not_an_msk_exception():
    article, article_url = page('ltt_sib', 'Один станок в поле', 'Статьи', 'katalog/stati/',
                                'В данной статье сравниваются два исполнения.')
    assert _card_gate('ltt_sib', article, article_url)[0] is CaptureStatus.NON_PRODUCT_EDITORIAL_PAGE
    product, product_url = page('ltt_sib', 'Фрезерный станок LTT-1000', 'Фрезерные станки', 'katalog/frezernye/',
                                'В данной статье мы сравниваем LTT-1000 и LTT-2000.')
    assert _card_gate('ltt_sib', product, product_url)[0] is CaptureStatus.SUCCESS


@pytest.mark.parametrize('filename', [
    'ltt_msk__machine_numeric_price.html',
    'ltt_sib__machine_por_outofstock.html',
])
def test_existing_product_fixtures_stay_success(filename):
    source, url = load(filename)
    assert _card_gate(filename.split('__')[0], source, url)[0] is CaptureStatus.SUCCESS


def test_missing_url_does_not_guess_an_editorial_role():
    html, _url = page('ltt_msk', 'Один станок в поле', 'Статьи', 'katalog/stati/',
                      'В данной статье сравниваются два исполнения.')
    assert _card_gate('ltt_msk', html, None)[0] is CaptureStatus.SUCCESS


def test_broken_price_stays_a_price_failure():
    html, url = page('ltt_msk', 'Один станок в поле', 'Статьи', 'katalog/stati/',
                     'В данной статье сравниваются два исполнения.', price='both')
    assert _card_gate('ltt_msk', html, url)[0] is CaptureStatus.PARSE_ERROR


def test_page_role_seal_matches_working_tree():
    from scripts.page_role_seals import ACCEPTED, PROPOSED, verify
    assert verify(baseline_seal=ACCEPTED, proposed_transition=PROPOSED)['proposed_transition'] == PROPOSED
