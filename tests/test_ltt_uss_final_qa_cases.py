"""Frozen mandatory case regressions for fresh QA, not semantic approval."""
from decimal import Decimal
import pytest
from scripts import qa_ltt_uss_final as q


def saved(index):
    e=next(e for e in q.r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko' and e['index']==index)
    path=q.o.ROOT/e['journal'];assert q.r.digest(path.read_bytes())==e['journal_sha256']
    row=q.r.load(path);outcome,p,c=q.o.extract(row)
    return row,outcome,p,c


@pytest.mark.parametrize('index,model,article,amount,stock',[
    (23,'Airstar 703/270/10 H','202 8753','189360','PreOrder'),
    (24,'Airstar 853/100','200 9831','168780','PreOrder'),
    (368,'HSWH 5','620 1001','2580','InStock'),
    (369,'HSWH Pro 20','620 1017','10620','InStock'),
    (374,'RWHA 2500','620 1109','23820','InStock'),
    (377,'SRWH-PRO 1500L','620 1116','36420','InStock'),
    (384,'TR 3','619 3030','10560','InStock')])
def test_all_seven_historical_corrected_rows(index,model,article,amount,stock):
    with q.q.guard() as network:
        row,outcome,p,c=saved(index)
    assert outcome=='PERSISTED' and p.name
    assert p.supplier_model==model and p.manufacturer_article==article
    assert p.price.current_price==Decimal(amount) and p.price.currency=='RUB' and p.availability==stock
    assert str(c.observed_at)==row['result']['observed_at']
    assert not any(network.values())


def test_row405_operator_confirmed_primary_section_fix():
    from universal_supplier.ltt_uss_capture import _card_gate
    e=next(e for e in q.r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko' and e['index']==405)
    row=q.r.load(q.o.ROOT/e['journal']);meta=row['result']
    body=(q.r.OUT/'EVIDENCE/uss_stanko'/(meta['evidence_sha256']+'.html')).read_bytes()
    with q.q.guard():
        p=q.r.parse('uss_stanko',body,e['url'])
        status,diag=_card_gate('uss_stanko',body.decode('utf8'),e['url'])
    assert status.value=='SUCCESS' and not diag
    assert p.supplier_model=='SSK 2' and p.manufacturer_article=='620 4001'
    assert p.price.current_price==49980 and p.availability=='PreOrder'


@pytest.mark.parametrize('suffix,amount,raw',[('uf100.htm',21312,'21 312 USD'),('uf200.htm',31212,'31 212 USD')])
def test_both_saved_explicit_usd_rows(suffix,amount,raw):
    e=next(e for e in q.r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko' and e['url'].endswith('/'+suffix))
    with q.q.guard():row,outcome,p,c=saved(e['index'])
    assert outcome=='PERSISTED' and p.price.current_price==amount and p.price.currency=='USD' and p.price.raw_text==raw
