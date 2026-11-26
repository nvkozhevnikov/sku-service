from pathlib import Path
from decimal import Decimal
from datetime import timedelta
import hashlib
import pytest
from scripts import run_ltt_uss_run1 as r
from universal_supplier.adapters.ltt_uss_html import price_value
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail
from universal_supplier.commercial import PriceState
from universal_supplier.commercial_persistence import HttpCapture,product_card_from_snapshot,observation_from_snapshot
from universal_supplier.ltt_uss_capture import _card_gate


def test_exact_saved_uf100_usd_flows_without_fx():
    body=(Path(__file__).parent/'fixtures/ltt_uss/uss_stanko__saved_uf100_usd.html').read_bytes()
    assert hashlib.sha256(body).hexdigest()=='09226377c88d943195afb49d0e4bb79678c47ea14f0aef4ab0d219af8777e2fb'
    url='https://uss-stanko.com/mach/frez/uf100.htm';product=parse_uss_stanko_detail(body.decode('utf8'),source_url=url)
    assert product.site_internal_id=='uss:mach/frez/uf100.htm' and product.source_url==url
    assert product.price.state==PriceState.NUMERIC_PUBLIC and product.price.current_price==Decimal('21312')
    assert product.price.currency=='USD' and product.price.raw_text=='21 312 USD'
    assert _card_gate('uss_stanko',body.decode('utf8'),url)[0].value=='SUCCESS'
    capture=HttpCapture(requested_url=url,final_url=url,http_status=200,content_type='text/html; charset=cp1251',
        observed_at=r.datetime.fromisoformat('2026-10-14T14:52:11.551415+00:00'),
        response_sha256='2cb5c8967574f295444fc59ded64c5b99b2b3f72d2da8f5487397123156850b0',evidence_sha256=hashlib.sha256(body).hexdigest(),evidence_ref='saved')
    card=product_card_from_snapshot(product,capture);obs=observation_from_snapshot(product,capture)
    assert card.price==obs.price==Decimal('21312') and card.currency==obs.currency=='USD'
    assert obs.price_raw=='21 312 USD' and card.quantity is None
    assert not any(k in card.raw_data for k in ('fx_rate','price_rub','converted_price'))


@pytest.mark.parametrize('raw',['21 312 EUR','21 312 XYZ','21 312 USD RUB','21 312 USD / 10','21 312 USD + 500 USD','от 21 312 USD','21 312 USD prose'])
def test_unknown_currency_conflict_or_prose_fails_closed(raw):
    price=price_value(raw,identity='id',url='https://test/p',path='cell')
    assert price.state==PriceState.PARSE_ERROR and price.current_price is None


def test_time_check_has_reserve_and_is_not_caught_as_network_failure():
    deadline=r.datetime.fromisoformat('2026-10-14T15:38:44+00:00')
    r.check_http_budget(deadline,clock=lambda:deadline-timedelta(seconds=91),reserve_seconds=90)
    with pytest.raises(r.BudgetReached):r.check_http_budget(deadline,clock=lambda:deadline,reserve_seconds=0)
    assert not issubclass(r.BudgetReached,Exception)


def test_no_get_after_pacing_reaches_cutoff(monkeypatch):
    run=r.Run.__new__(r.Run);run.deadline=r.datetime.fromisoformat('2026-10-14T15:38:44+00:00')
    calls=[];run.pacer=type('Pacer',(),{'before':lambda self:calls.append('paced')})()
    def check(*a,**kw):
        if calls:raise r.BudgetReached()
    monkeypatch.setattr(r,'check_http_budget',check)
    monkeypatch.setattr(r,'code_hashes',lambda:pytest.fail('Must stop before HTTP event'))
    with pytest.raises(r.BudgetReached):run.before_request('uss_stanko','https://uss-stanko.com/mach/test.htm')
    assert calls==['paced']
