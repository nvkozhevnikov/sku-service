from decimal import Decimal
import json
import pytest
from tests.test_ltt_uss_adapters import load
from universal_supplier.adapters.ltt_yug_role import classify_yug_page, NonProductYugPage
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.ltt_uss_capture import _card_gate, is_product_candidate
from universal_supplier.http_capture import CaptureStatus
from scripts import run_ltt_uss_live_canary as runner


def test_exact_category_cannot_enter_product_writer():
    source, url = load('ltt_yug__saved_empty_category_id1.html')
    assert classify_yug_page(source, url) == 'CATEGORY_PAGE'
    assert _card_gate('ltt_yug', source, url)[0] == CaptureStatus.NON_PRODUCT_CATEGORY_PAGE
    assert not is_product_candidate('ltt_yug', url)[0]
    assert not is_product_candidate('ltt_yug', url.replace('://', '://www.'))[0]
    with pytest.raises(NonProductYugPage, match='CATEGORY_PAGE'):
        parse_ltt_yug_detail(source, source_url=url)


@pytest.mark.parametrize('pid,price,stock', [('312','4210148.50','InStock'),('297','1905000','OutOfStock')])
def test_real_distinct_products_remain_persistable(pid,price,stock):
    source,url=load('ltt_yug__saved_'+pid+'.html')
    assert classify_yug_page(source,url)=='PRODUCT_PAGE'
    assert _card_gate('ltt_yug',source,url)[0]==CaptureStatus.SUCCESS
    product=parse_ltt_yug_detail(source,source_url=url)
    assert product.site_internal_id==pid and product.source_category
    assert product.price.current_price==Decimal(price) and product.availability==stock


def test_category_decision_does_not_exclude_unrelated_sparse_product():
    source,url=load('ltt_yug__saved_empty_category_id1.html')
    unrelated=url.replace('derevoobrabatyvayuschee-oborudovanie','another-item')
    assert classify_yug_page(source,unrelated)=='PRODUCT_PAGE'
    assert is_product_candidate('ltt_yug',unrelated)[0]


def test_changed_confirmed_entity_fails_closed():
    source,url=load('ltt_yug__saved_empty_category_id1.html')
    changed=source.replace('/img/noImg.jpg','/uploads/item.jpg')
    assert classify_yug_page(changed,url)=='AMBIGUOUS_PAGE_ROLE'
    assert _card_gate('ltt_yug',changed,url)[0]==CaptureStatus.AMBIGUOUS
    with pytest.raises(NonProductYugPage):parse_ltt_yug_detail(changed,source_url=url)


@pytest.mark.parametrize('status,expected_gets',[(CaptureStatus.NETWORK_ERROR,1),(CaptureStatus.AMBIGUOUS,20)])
def test_driver_never_continues_other_supplier_after_failed_gate(monkeypatch,tmp_path,status,expected_gets):
    from types import SimpleNamespace
    from datetime import datetime,timezone
    config={'host':'127.0.0.1','port':55455,'dbname':'universal_supplier_ltt_uss','user':'synthetic','password':'synthetic','sslmode':'disable'}
    (tmp_path/'PRIVATE').mkdir();(tmp_path/'PRIVATE/connection.json').write_text(json.dumps({'runtime':config}))
    out=tmp_path/'OUT';out.mkdir()
    plans={s:{'urls':[f'https://example.invalid/{n}' for n in range(20)]} for s in runner.SOURCES}
    class Repo:
        connection=None
        def __init__(self,*a):self.connection=self
        def execute(self,*a):return self
        def fetchall(self):return []
        def fetchone(self):return (2,)
        def close(self):pass
        def persist_commercial_observation(self,*a):pytest.fail('Unexpected product write')
    calls=[]
    def capture(url,**kwargs):
        calls.append(kwargs['supplier'])
        return SimpleNamespace(status=status,final_url=None,http_status=None,observed_at=datetime.now(timezone.utc),
            response_sha256=None,evidence_sha256=None,evidence_ref=None,charset=None,diagnostics=(),transport_url=None,evidence_body=None)
    monkeypatch.setattr(runner,'BASE',tmp_path);monkeypatch.setattr(runner,'OUT',out)
    monkeypatch.setattr(runner,'plan',lambda:plans);monkeypatch.setattr(runner,'PostgresRepository',Repo)
    monkeypatch.setattr(runner,'fingerprints',lambda c:{})
    monkeypatch.setattr(runner,'PublicPacer',lambda n:SimpleNamespace(before_get=lambda:None))
    monkeypatch.setattr(runner,'capture_source_html',capture)
    assert runner.main()==1
    assert calls==['ltt_msk']*expected_gets
    ledger=json.loads((out/'LIVE_CANARY.json').read_text())
    assert ledger['source']=='ltt_msk' and not ledger['run1_started']
