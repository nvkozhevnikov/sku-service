from urllib.parse import parse_qsl,urlsplit
from urllib.error import HTTPError
from io import BytesIO
from unittest.mock import patch
import pytest
from universal_supplier.http_capture import public_transport_url,UrllibPublicHttpClient,_BoundedRedirectHandler


@pytest.mark.parametrize('source,wire',[
    ('https://example.test/a-°-b','https://example.test/a-%C2%B0-b'),
    ('https://example.test/станок','https://example.test/%D1%81%D1%82%D0%B0%D0%BD%D0%BE%D0%BA'),
    ('https://example.test/a b/(c),x:1+2','https://example.test/a%20b/(c),x:1+2'),
    ('https://example.test/a-%C2%B0-b','https://example.test/a-%C2%B0-b'),
    ('https://EXAMPLE.test:443/A%2fB','https://EXAMPLE.test:443/A%2fB'),
    ('https://example.test/plain/ascii','https://example.test/plain/ascii'),
    ('https://example.test/a#локальный','https://example.test/a'),
    ('https://example.test/a%literal','https://example.test/a%25literal'),
])
def test_component_encoding(source,wire):
    assert public_transport_url(source)==wire
    assert public_transport_url(wire)==wire


def test_query_semantics():
    source='https://example.test/card?q=станок °&x=a+b&x=a%2Bb&raw=%25'
    wire=public_transport_url(source)
    assert parse_qsl(urlsplit(source).query)==parse_qsl(urlsplit(wire).query)
    assert '%25C2' not in wire and '%D1%81' in wire


class Response:
    status=200
    headers={'Content-Type':'text/html; charset=utf-8'}
    def __init__(self,url):self.url=url
    def geturl(self):return self.url
    def read(self):return b'<h1>machine</h1>'
    def __enter__(self):return self
    def __exit__(self,*args):pass


def test_real_client_ascii_wire_keeps_source_identity():
    source='https://example.test/a-°-b'
    captured=[]
    class Opener:
        def open(self,request,timeout):
            request.selector.encode('ascii')
            captured.append(request.full_url)
            return Response(request.full_url)
    with patch('universal_supplier.http_capture.build_opener',return_value=Opener()):
        result=UrllibPublicHttpClient().get(source,timeout_seconds=1)
    assert captured==['https://example.test/a-%C2%B0-b']
    assert result.final_url==source
    assert result.transport_url==captured[0]
    assert result.redirects==()


def test_existing_product_query_and_fragment_rejections_remain():
    for source in ('https://example.test/a?q=1','https://example.test/a#b'):
        with patch('universal_supplier.http_capture.build_opener') as opener:
            with pytest.raises(ValueError):UrllibPublicHttpClient().get(source,timeout_seconds=1)
            opener.assert_not_called()


def test_custom_query_validator_and_redirect_wire():
    handler=_BoundedRedirectHandler(lambda url:None)
    from urllib.request import Request
    result=handler.redirect_request(Request('https://example.test/a'),None,302,'Found',{},'https://example.test/путь?q=°')
    assert result.full_url==public_transport_url('https://example.test/путь?q=°')
    assert handler.redirects==['https://example.test/путь?q=°']
