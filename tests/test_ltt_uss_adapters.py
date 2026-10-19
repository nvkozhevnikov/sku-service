"""Frozen source regressions and explicitly derived adversarial cases; HTTP=0."""
from decimal import Decimal
from pathlib import Path
import json
import hashlib
import pytest
from universal_supplier.adapters.ltt import parse_ltt_modx_detail
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail
from universal_supplier.commercial import PriceState
from universal_supplier.ltt_uss_capture import _card_gate

FIXTURES = Path(__file__).parent / 'fixtures/ltt_uss'
PROVENANCE = json.loads((FIXTURES / 'PROVENANCE.json').read_text('utf-8'))


def load(filename):
    item = next(p for p in PROVENANCE if p['filename'] == filename)
    body = (FIXTURES / filename).read_bytes()
    assert hashlib.sha256(body).hexdigest() == item['fixture_sha256']
    return body.decode(item['charset']), item['source_url']


@pytest.mark.parametrize('filename,pid,value,availability', [
    ('ltt_msk__machine_numeric_price.html', '65', '2693682', 'InStock'),
    ('ltt_msk__spare_accessory.html', '1813', '2800', 'InStock'),
    ('ltt_msk__tool_blade.html', '1877', '2500', 'InStock'),
    ('ltt_msk__compressor.html', '1874', '241000', 'InStock'),
    ('ltt_sib__machine_numeric_mb203c.html', '2271', '221925', 'InStock'),
    ('ltt_sib__machine_por_outofstock.html', '431', None, 'OutOfStock'),
    ('ltt_sib__consumable_drill.html', '1775', None, 'InStock'),
    ('ltt_sib__metal_bandsaw.html', '2227', None, 'OutOfStock'),
])
def test_modx_frozen(filename, pid, value, availability):
    source, url = load(filename)
    product = parse_ltt_modx_detail(source, supplier=filename.split('__')[0], source_url=url)
    assert product.name and product.source_category and product.site_internal_id == pid
    assert product.price.current_price == (Decimal(value) if value else None)
    assert product.price.state == (PriceState.NUMERIC_PUBLIC if value else PriceState.PRICE_ON_REQUEST)
    assert product.availability == availability


@pytest.mark.parametrize('filename,pid,value,availability', [
    ('ltt_yug__machine_used_qty1.html', '357', '290000', 'InStock'),
    ('ltt_yug__machine_fugoval_jp801.html', '442', None, 'OutOfStock'),
    ('ltt_yug__machine_numeric_mf362.html', '414', '771181.61', 'InStock'),
    ('ltt_yug__machine_nonrub_slug_variant.html', '355', None, 'OutOfStock'),
])
def test_yug_frozen(filename, pid, value, availability):
    source, url = load(filename)
    product = parse_ltt_yug_detail(source, source_url=url)
    assert product.site_internal_id == pid and product.name and product.source_category
    assert product.price.current_price == (Decimal(value) if value else None)
    assert product.price.state == (PriceState.NUMERIC_PUBLIC if value else PriceState.PRICE_ON_REQUEST)
    assert product.availability == availability


def test_yug_never_borrows_related_identity():
    source, url = load('ltt_yug__machine_numeric_mf362.html')
    product = parse_ltt_yug_detail(source.replace('data-product="414"', 'data-product=""'), source_url=url)
    assert product.site_internal_id is None


def test_uss_cp1251_order():
    source, url = load('uss_stanko__dmtg_detail.html')
    product = parse_uss_stanko_detail(source, source_url=url)
    assert product.name == '5-осевой фрезерный обрабатывающий центр VDW50'
    assert product.source_url == url and product.source_brand == 'DMTG'
    assert product.price.state == PriceState.PRICE_ON_REQUEST and product.price.current_price is None
    assert product.availability == 'PreOrder'


@pytest.mark.parametrize('filename', ['uss_stanko__mrcm_detail.html', 'uss_stanko__unikraft_accessory.html'])
def test_uss_multi_variant_not_single_product(filename):
    source, url = load(filename)
    product = parse_uss_stanko_detail(source, source_url=url)
    assert 'MULTI_VARIANT' in product.diagnostics
    assert product.site_internal_id is None and product.availability is None
    assert product.price.state == PriceState.AMBIGUOUS and product.price.current_price is None
    assert _card_gate('uss_stanko', source)[0].value == 'AMBIGUOUS'


def test_uss_discontinued_derived():
    source, url = load('uss_stanko__dmtg_detail.html')
    product = parse_uss_stanko_detail(source.replace('под заказ', 'Снят с производства'), source_url=url)
    assert product.availability == 'OutOfStock'


@pytest.mark.parametrize('amount,state,value', [('125 000', PriceState.NUMERIC_PUBLIC, '125000'),
    ('125 000,50', PriceState.NUMERIC_PUBLIC, '125000.50'), ('0', PriceState.ZERO_INVALID, None),
    ('32 940 39 420 140 460', PriceState.PARSE_ERROR, None)])
def test_uss_numeric_derived(amount, state, value):
    source, url = load('uss_stanko__dmtg_detail.html')
    source = source.replace('по запросу', amount).replace('Цена (с учетом НДС)', 'Цена (с учетом НДС), руб')
    product = parse_uss_stanko_detail(source, source_url=url)
    assert product.price.state == state
    assert product.price.current_price == (Decimal(value) if value else None)


def test_uss_category_is_not_card():
    source, _ = load('uss_stanko__category_radial_drilling_operator_example.html')
    assert _card_gate('uss_stanko', source)[0].value == 'AMBIGUOUS'


def test_uss_canonical_host_rejected():
    source, url = load('uss_stanko__dmtg_detail.html')
    with pytest.raises(ValueError):
        parse_uss_stanko_detail(source.replace('https://uss-stanko.com/', 'https://foreign.test/'), source_url=url)
