"""Exact saved SIB regressions plus fail-closed unit grammar; supplier HTTP=0."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import pytest
from universal_supplier.adapters.ltt_uss_html import price_value
from universal_supplier.adapters.ltt import parse_ltt_modx_detail
from universal_supplier.commercial import PriceState
from universal_supplier.ltt_uss_capture import _card_gate
from universal_supplier.http_capture import CaptureStatus
from universal_supplier.commercial_persistence import HttpCapture,product_card_from_snapshot,observation_from_snapshot
from scripts import run_ltt_uss_run1 as run1

FIXTURES=Path(__file__).parent/'fixtures/ltt_uss_units'
PROVENANCE=json.loads((FIXTURES/'PROVENANCE.json').read_text('utf8'))


def extract(raw):return price_value(raw,identity='unchanged',url='https://source.test/p',path='own')


@pytest.mark.parametrize('item',PROVENANCE,ids=lambda r:r['id'])
def test_exact_saved_sib_price_and_identity_no_unit_arithmetic(item):
    body=(FIXTURES/item['filename']).read_bytes()
    assert hashlib.sha256(body).hexdigest()==item['fixture_sha256']
    product=parse_ltt_modx_detail(body.decode('utf8'),supplier='ltt_sib',source_url=item['url'])
    assert product.site_internal_id==item['id'] and product.source_url==item['url']
    assert product.price.state==PriceState.NUMERIC_PUBLIC and product.price.current_price==Decimal(item['expected_price'])
    assert product.price.currency=='RUB' and product.price.raw_text==item['raw']
    assert product.price.old_price is None and product.price.diagnostics==('sales_unit_raw:бухта',)
    assert _card_gate('ltt_sib',body.decode('utf8'),item['url'])[0]==CaptureStatus.SUCCESS
    capture=HttpCapture(requested_url=item['url'],final_url=item['url'],http_status=200,content_type='text/html; charset=utf-8',
        observed_at=run1.datetime.fromisoformat(item['original_time']),response_sha256=item['response_sha256'],
        evidence_sha256=item['fixture_sha256'],evidence_ref='saved')
    card=product_card_from_snapshot(product,capture);obs=observation_from_snapshot(product,capture)
    assert card.quantity is None and card.price==obs.price==Decimal(item['expected_price'])
    assert card.raw_data['price_extraction']['raw_text']==item['raw']
    assert obs.extraction_evidence['diagnostics']==('sales_unit_raw:бухта',)
    assert 'price_per_meter' not in card.raw_data and 'meters_per_coil' not in card.raw_data


@pytest.mark.parametrize('raw,amount,unit',[('3 500 ₽ / бухта','3500','бухта'),('4 000 руб. / шт.','4000','шт.'),
    ('5 000 ₽/комплект','5000','комплект'),('3\u00a0500 ₽ / БУХТА','3500','БУХТА'),('125,50 руб. / м','125.50','м')])
def test_generic_single_explicit_amount_and_raw_unit(raw,amount,unit):
    p=extract(raw);assert p.current_price==Decimal(amount) and p.currency=='RUB'
    assert p.raw_text==raw and p.diagnostics==('sales_unit_raw:'+unit,)


@pytest.mark.parametrize('raw',['3500','3 500 ₽','4 000 руб.','125,50 руб.'])
def test_plain_currency_suffix_unchanged(raw):
    p=extract(raw);assert p.state==PriceState.NUMERIC_PUBLIC and p.diagnostics==() and p.raw_text==raw


@pytest.mark.parametrize('raw,state',[('',PriceState.MISSING),('Цена по запросу',PriceState.PRICE_ON_REQUEST),('0 ₽',PriceState.ZERO_INVALID)])
def test_missing_por_and_zero_unchanged(raw,state):
    assert extract(raw).state==state and extract(raw).current_price is None


@pytest.mark.parametrize('raw',['3500 ₽ / 10 м','3500 ₽ / бухта 10м','3500 ₽ / бухта / м','3500 ₽ / шт. или комплект',
    '3500 ₽ / 2 * 5 м','3500 / 10','3500 ₽ / непонятно','от 3500 ₽ / бухта','3500–4000 ₽ / бухта',
    '3500 ₽ 4000 ₽','старая 4000 ₽ новая 3500 ₽','3500 ₽ / м = 350 ₽'])
def test_unsupported_multi_amount_range_and_arithmetic_fail_closed(raw):
    p=extract(raw);assert p.state==PriceState.PARSE_ERROR and p.current_price is None
    assert p.raw_text==raw and p.diagnostics==('unsupported_price_grammar',)


def test_parser_error_is_distinct_from_source_ambiguity():
    from bs4 import BeautifulSoup
    item=PROVENANCE[0];dom=BeautifulSoup((FIXTURES/item['filename']).read_text('utf8'),'html.parser')
    dom.select_one('.product .product_price').string='3500 ₽ / unknown';source=str(dom)
    status,diagnostics=_card_gate('ltt_sib',source,item['url'])
    assert status==CaptureStatus.PARSE_ERROR and 'price_parser_error' in diagnostics
    assert _card_gate('ltt_sib','<h1>no stable ID</h1>')[0]==CaptureStatus.AMBIGUOUS


def test_reviewed_overlay_keeps_historical_journal_and_rejects_tampering(tmp_path,monkeypatch):
    monkeypatch.setattr(run1,'OUT',tmp_path)
    url='https://lttsib.ru/katalog/p';name='000000_'+run1.digest(url.encode())[:16]+'.json'
    original=tmp_path/'ROWS/ltt_sib'/name;run1.save(original,{'outcome':'AMBIGUOUS'})
    revised=tmp_path/'REVISED_ROWS/ltt_sib'/name;run1.save(revised,{'outcome':'PERSISTED'})
    run=run1.Run.__new__(run1.Run);run.scopes={'ltt_sib':[{'canonical_url':url}]}
    run.state={'reviewed_row_revisions':{'ltt_sib':{'0':{'path':str(revised.relative_to(tmp_path)),
        'original_journal_sha256':run1.digest(original.read_bytes())}}}}
    assert run.journal_path('ltt_sib',0)==revised and run1.load(original)['outcome']=='AMBIGUOUS'
    run1.save(original,{'outcome':'PERSISTED'})
    with pytest.raises(AssertionError):run.journal_path('ltt_sib',0)
