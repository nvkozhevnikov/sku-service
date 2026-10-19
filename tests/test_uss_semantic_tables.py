from pathlib import Path
import hashlib
import json
from decimal import Decimal
import pytest
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail, _commercial_rows
from universal_supplier.ltt_uss_capture import _card_gate

FIXTURES=Path(__file__).parent/'fixtures/uss_semantic_tables'
PROVENANCE=json.loads((FIXTURES/'PROVENANCE.json').read_text('utf8'))


@pytest.mark.parametrize('index,amount,model,article',[
    (23,'189360','Airstar 703/270/10 H','202 8753'),
    (24,'168780','Airstar 853/100','200 9831')])
def test_exact_frozen_single_product_regression(index,amount,model,article):
    item=next(v for v in PROVENANCE if v['index']==index)
    body=(FIXTURES/item['filename']).read_bytes()
    assert hashlib.sha256(body).hexdigest()==item['sha256']
    p=parse_uss_stanko_detail(body.decode('utf8'),source_url=item['url'])
    assert p.price.current_price==Decimal(amount) and p.price.currency=='RUB'
    assert p.availability=='PreOrder' and p.supplier_model==model and p.manufacturer_article==article
    assert _card_gate('uss_stanko',body.decode('utf8'),item['url'])[0].value=='SUCCESS'
    assert p.site_internal_id=='uss:'+item['url'].split('.com/')[1]


def table(model='ZX900',article='123 456',price='3 500',classes=''):
    return f'''<table {classes}><tr><td>Модель</td><td>{model}</td></tr>
      <tr><td>Номер арт.</td><td>{article}</td></tr>
      <tr><td>Цена (с учетом НДС), руб.</td><td>{price}</td></tr>
      <tr><td>Наличие</td><td><a name="art{article.replace(' ','')}"></a>под заказ</td></tr>
      <tr><td>Мощность</td><td>4 кВт</td></tr></table>'''


def product(content):
    return parse_uss_stanko_detail('<h1>Станок ZX900</h1>'+content,source_url='https://uss-stanko.com/mach/test/product.htm')


def test_generic_model_not_airstar_article_or_url_special_case():
    p=product(table())
    assert p.supplier_model=='ZX900' and p.manufacturer_article=='123 456'
    assert p.price.current_price==3500 and p.availability=='PreOrder'


@pytest.mark.parametrize('content',[
    '<table><tr><td>Меню</td><td>Цена</td></tr></table>',
    table()+table(model='ZX901',article='222 333'),
    table().replace('name="art123456"','name="other"'),
    table(model='UNRELATED100'),
    table().replace('<td>ZX900</td>','<td>ZX900</td><td>ZX901</td>'),
    table().replace('<td>123 456</td>','<td></td>')])
def test_layout_ambiguity_and_variant_matrix_fail_closed(content):
    p=product(content)
    assert p.price.current_price is None
    assert p.manufacturer_article is None
    assert not _commercial_rows('<h1>Станок ZX900</h1>'+content)


@pytest.mark.parametrize('raw,state,amount,currency',[
    ('по запросу','price_on_request',None,None),
    ('','missing',None,None),
    ('21 312 USD','numeric_public',21312,'USD')])
def test_explicit_published_commercial_grammar(raw,state,amount,currency):
    # Empty source price is deliberately insufficient for this new fallback.
    p=product(table(price=raw))
    assert p.price.state.value==state and p.price.current_price==amount
    assert p.price.currency==currency


def test_discontinued_overrides_stock_without_rewriting_source_wording():
    p=product(table()+'<p>Снят с производства</p>')
    assert p.availability=='OutOfStock'


def test_existing_table_data_contract_ignores_other_classless_tables():
    p=product(table(classes='class="data"')+table(model='OTHER1',price='1'))
    assert p.price.current_price==3500 and p.manufacturer_article is None


def test_existing_table_data_multiple_variants_never_flattened():
    p=product(table(classes='class="data"')+table(model='ZX901',article='222 333',classes='class="data"'))
    assert 'MULTI_VARIANT' in p.diagnostics and p.site_internal_id is None
    assert p.price.current_price is None
