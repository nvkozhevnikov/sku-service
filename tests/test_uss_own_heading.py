from dataclasses import asdict
from decimal import Decimal
import pytest
from bs4 import BeautifulSoup
from scripts import correct_uss_own_heading as h
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail as parse
from universal_supplier.ltt_uss_capture import _card_gate

URL='https://uss-stanko.com/mach/test/own.htm'


def page():
    return f'''<html><head><link rel="canonical" href="{URL}"></head><body>
    <div id="div_print"><h2 class="zagolovok_u">Станок ZX900.</h2>
    <table class="data"><tr><td>Модель</td><td>ZX900</td></tr>
    <tr><td>Номер арт.</td><td>123 456</td></tr>
    <tr><td>Цена, руб.</td><td>3500</td></tr>
    <tr><td>Наличие</td><td><a name="art123456"></a>на складе</td></tr>
    <tr><td></td><td><a class="bnt_to_cart" onclick="add_to_cart('1','2','')">В корзину</a></td></tr>
    </table></div></body></html>'''


def test_generic_own_heading_not_specific_model_article_or_url():
    p=parse(page(),source_url=URL)
    assert p.name=='Станок ZX900.' and p.supplier_model=='ZX900'
    assert p.manufacturer_article=='123 456' and p.price.current_price==3500
    assert p.site_internal_id=='uss:mach/test/own.htm'
    assert _card_gate('uss_stanko',page(),URL)[0].value=='SUCCESS'


@pytest.mark.parametrize('change',[
    lambda s:s.replace('id="div_print"','id="navigation"'),
    lambda s:s.replace('<h2 class="zagolovok_u">','<h2>'),
    lambda s:s.replace('</h2>','</h2><h2 class="zagolovok_u">Другой ZX901</h2>'),
    lambda s:s.replace('Станок ZX900.','Технические характеристики ZX900'),
    lambda s:s.replace('Станок ZX900.','Новости ZX900'),
    lambda s:s.replace('Станок ZX900.','Каталог ZX900'),
    lambda s:s.replace('Станок ZX900.','Серия ZX900'),
    lambda s:s.replace('Станок ZX900.','Станок ZX9000.'),
    lambda s:s.replace('name="art123456"','name="art999"'),
    lambda s:s.replace('bnt_to_cart','unrelated'),
    lambda s:s.replace(URL,'https://uss-stanko.com/mach/test/other.htm'),
    lambda s:s.replace('<td>ZX900</td>','<td>ZX900</td><td>ZX901</td>'),
    lambda s:s.replace('<td>123 456</td>','<td></td>'),
    lambda s:s.replace('</table>','</table><table class="data"><tr><td>Модель</td><td>ZX901</td></tr></table>'),
    lambda s:s.replace('<div id="div_print">','<nav><div id="div_print">').replace('</div>','</div></nav>'),
])
def test_unsafe_section_group_navigation_or_ambiguous_shape_not_promoted(change):
    p=parse(change(page()),source_url=URL)
    assert p.name==''
    assert _card_gate('uss_stanko',change(page()),URL)[0].value=='AMBIGUOUS'


def test_h1_contract_unaffected_by_fallback_context():
    body=page().replace('h2','h1')
    p=parse(body,source_url=URL)
    assert p.name=='Станок ZX900.' and p.manufacturer_article is None
    assert 'uss_own_h2_verified' not in p.diagnostics


def test_exact_saved_row368_regression():
    a=next(a for a in h.r.load(h.PHASE/'TITLE_AUDIT_BEFORE.json')['rows'] if a['index']==368)
    body=(h.r.OUT/'EVIDENCE/uss_stanko'/(a['evidence_sha256']+'.html')).read_bytes()
    assert h.r.digest(body)==a['evidence_sha256']
    p=parse(body.decode('utf8'),source_url=a['url'])
    assert p.name=='Бутылочный домкрат HSWH 5.' and p.supplier_model=='HSWH 5'
    assert p.manufacturer_article=='620 1001' and p.price.current_price==Decimal(2580)
    assert p.price.currency=='RUB' and p.availability=='InStock'
    assert p.site_internal_id=='uss:unikraft/domkrat/hswh.htm'
    assert _card_gate('uss_stanko',body.decode('utf8'),a['url'])[0].value=='SUCCESS'


def test_saved_primary_with_explicit_additional_accessories_has_local_identity():
    a=next(a for a in h.r.load(h.PHASE/'TITLE_AUDIT_BEFORE.json')['rows'] if a['index']==405)
    body=(h.r.OUT/'EVIDENCE/uss_stanko'/(a['evidence_sha256']+'.html')).read_bytes()
    p=parse(body.decode('utf8'),source_url=a['url'])
    assert p.name=='Камера пескоструйной очистки SSK 2.'
    assert p.supplier_model=='SSK 2' and p.manufacturer_article=='620 4001'
    assert p.price.current_price==49980 and p.price.currency=='RUB' and p.availability=='PreOrder'
    assert _card_gate('uss_stanko',body.decode('utf8'),a['url'])[0].value=='SUCCESS'
