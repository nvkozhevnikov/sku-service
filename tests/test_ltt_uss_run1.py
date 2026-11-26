import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from scripts import run_ltt_uss_run1 as run1
from tests.test_ltt_uss_adapters import load
from universal_supplier.http_capture import _BoundedRedirectHandler,UrllibPublicHttpClient,HttpResponse
from universal_supplier.ltt_uss_capture import sanitise_for_charset


def test_random_pacing_every_subsequent_request_and_no_fixed_twenty():
    waits=[];samples=iter([2.0,7.0,4.25])
    pacer=run1.RandomPacer(uniform=lambda a,b:next(samples),sleep=waits.append)
    assert pacer.before() is None
    assert [pacer.before() for _ in range(3)]==[2.0,7.0,4.25]
    assert waits==[2.0,7.0,4.25]


def test_redirect_callback_and_initial_request_are_both_paced():
    from urllib.request import Request
    calls=[]
    handler=_BoundedRedirectHandler(lambda u:None,calls.append)
    handler.redirect_request(Request('https://example.test/a'),None,302,'Found',{},'https://example.test/b')
    class Response:
        status=200;headers={'Content-Type':'text/html'}
        def geturl(self):return 'https://example.test/a'
        def read(self):return b'<h1>item</h1>'
        def __enter__(self):return self
        def __exit__(self,*a):pass
    with patch('universal_supplier.http_capture.build_opener',return_value=SimpleNamespace(open=lambda *a,**kw:Response())):
        UrllibPublicHttpClient(calls.append).get('https://example.test/a',timeout_seconds=1)
    assert calls==['https://example.test/b','https://example.test/a']


def test_csrf_metadata_redacted_both_attribute_orders():
    body=b'<meta name="csrf-token" content="private1"><meta content="private2" name="_token"><meta name="description" content="machine">'
    clean=sanitise_for_charset(body,'utf-8')
    assert b'private1' not in clean and b'private2' not in clean and b'machine' in clean


@pytest.mark.parametrize('already_committed',[True,False])
def test_exact_prepared_event_recovery_avoids_duplicate_transaction(monkeypatch,already_committed):
    calls=[]
    class Repo:
        connection=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[(17,)] if already_committed else []))
        def persist_commercial_observation(self,*args):calls.append('write')
    receipt=dict(source_product_id=1,offer_id=2,capture_id=17,observation_id=19)
    monkeypatch.setattr(run1,'check_observation',lambda *a:receipt)
    result,recovered=run1.guarded_commit(Repo(),SimpleNamespace(supplier='ltt_yug'),SimpleNamespace(fingerprint='exact-event'))
    assert result==receipt and recovered==already_committed
    assert calls==([] if already_committed else ['write'])


def test_unresolved_inflight_request_never_gets_replayed(tmp_path,monkeypatch):
    run=run1.Run.__new__(run1.Run);path=tmp_path/'row.json'
    row=dict(phase='REQUESTING',supplier='ltt_msk',url='https://ltt-msk.ru/katalog/item',intent=dict(attempt=1),attempts=[])
    monkeypatch.setattr(run1,'capture_source_html',lambda *a,**k:pytest.fail('Unexpected HTTP replay'))
    with pytest.raises(RuntimeError,match='INFLIGHT_REQUEST_UNRESOLVED'):run.fetch(row,path)


def test_durable_response_recovery_uses_saved_bytes_http_zero(tmp_path,monkeypatch):
    monkeypatch.setattr(run1,'OUT',tmp_path)
    source,url=load('ltt_msk__machine_numeric_price.html');raw=source.encode('utf8');rawpath=tmp_path/'raw.bin';run1.atomic_bytes(rawpath,raw)
    path=tmp_path/'row.json';response_path=tmp_path/'row.response1.json'
    run1.save(response_path,dict(response=dict(final_url=url,status_code=200,content_type='text/html; charset=utf-8',redirects=[],transport_url=url),response_sha256=run1.digest(raw),raw_path='raw.bin'))
    row=dict(phase='REQUESTING',supplier='ltt_msk',url=url,intent=dict(attempt=1,observed_at='2026-10-14T00:00:00+00:00'),attempts=[])
    run=run1.Run.__new__(run1.Run)
    monkeypatch.setattr(run1,'UrllibPublicHttpClient',lambda *a:pytest.fail('Unexpected supplier HTTP'))
    assert run.fetch(row,path).value=='SUCCESS'
    saved=run1.load(path);assert saved['phase']=='CAPTURED'
    assert saved['result']['observed_at']=='2026-10-14 00:00:00+00:00'
    assert saved['result']['response_sha256']==run1.digest(raw)


def test_atomic_journal_preserves_terminal_identity(tmp_path):
    path=tmp_path/'row.json'
    run1.save(path,dict(phase='PREPARED',capture_fingerprint='one-event',external_id='312'))
    run1.save(path,dict(phase='TERMINAL',capture_fingerprint='one-event',external_id='312'))
    assert run1.load(path)['external_id']=='312' and not path.with_suffix('.json.partial').exists()


def test_fresh_response_prepares_in_memory_then_restores_exact_event(tmp_path,monkeypatch):
    monkeypatch.setattr(run1,'OUT',tmp_path)
    source,url=load('ltt_msk__machine_numeric_price.html');raw=source.encode('utf8')
    class Client:
        def __init__(self,*a):pass
        def get_validated(self,url,**kw):return HttpResponse(url,200,'text/html; charset=utf-8',raw)
    monkeypatch.setattr(run1,'UrllibPublicHttpClient',Client)
    run=run1.Run.__new__(run1.Run)
    run.c=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[],fetchone=lambda:None))
    journal=dict(phase='NEW',supplier='ltt_msk',url=url);path=tmp_path/'row.json'
    assert run.fetch(journal,path).value=='SUCCESS'
    assert isinstance(journal['result']['capture']['observed_at'],run1.datetime)
    run.prepare(journal,path)
    assert journal['phase']=='PREPARED' and journal['outcome']=='PERSISTED'
    loaded=run1.load(path);product,capture=run.product(loaded)
    assert capture.fingerprint==journal['capture_fingerprint'] and product.site_internal_id=='65'
