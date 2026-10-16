from tests.test_ltt_uss_adapters import load
from universal_supplier.adapters.ltt import parse_ltt_modx_detail
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.adapters.ltt_uss_html import source_model
import pytest


@pytest.mark.parametrize('title,expected', [('Станок MB203C, (300мм)','MB203C'),('Фуговальный станок JP 801','JP 801'),
    ('Фреза радиусная ф40 для станка',None),('Станок HJD-M623A (р)','HJD-M623A')])
def test_model_is_not_dimension(title,expected):
    assert source_model(title)==expected


def test_frozen_sib_dimension_and_raw_stock():
    source,url=load('ltt_sib__machine_numeric_mb203c.html')
    product=parse_ltt_modx_detail(source,supplier='ltt_sib',source_url=url)
    assert product.supplier_model=='MB203C'
    assert product.enrichment_evidence['availability_raw']=='в наличии'


@pytest.mark.parametrize('fixture,kind',[('ltt_msk__spare_accessory.html','spare'),('ltt_msk__tool_blade.html','tooling')])
def test_accessory_source_evidence(fixture,kind):
    source,url=load(fixture)
    product=parse_ltt_modx_detail(source,supplier='ltt_msk',source_url=url)
    assert product.enrichment_evidence['product_kind']['product_kind']==kind
    assert product.enrichment_evidence['source_title']==product.name


def test_yug_separated_model_and_raw_stock():
    source,url=load('ltt_yug__machine_fugoval_jp801.html')
    product=parse_ltt_yug_detail(source,source_url=url)
    assert product.supplier_model=='JP 801'
    assert product.enrichment_evidence['availability_raw']=='Нет в наличии'
