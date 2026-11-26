import asyncio
from types import SimpleNamespace
import socket
import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from universal_supplier.url_safety import validate_public_http_url,UnsafeUrlError
from universal_supplier.public_connection import public_socket,PublicHTTPSConnection
from universal_supplier.control_plane.web import optional_int,create_app
from universal_supplier.control_plane.store import ProductFilters
from test_readiness_panel import PanelStore

@pytest.mark.parametrize('url',[
    'http://127.0.0.1/','http://[::ffff:10.1.2.3]/','http://100.64.0.1/',
    'http://169.254.169.254/','https://user:pw@optimum.su/',
    'https://optimum.su:8443/','https://optimum.su\n.evil/','file:///x',
])
def test_unsafe_url_rejected_without_dns(url):
    with pytest.raises(UnsafeUrlError):validate_public_http_url(url)

@pytest.mark.parametrize('addresses',[[],['10.0.0.1'],['93.184.216.34','127.0.0.1']])
def test_dns_empty_or_any_private_answer_is_rejected(addresses):
    resolver=lambda *args:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in addresses]
    with pytest.raises(UnsafeUrlError):validate_public_http_url('https://optimum.su/',resolve_dns=True,resolver=resolver)

def test_connect_uses_validated_literal_without_second_dns(monkeypatch):
    queries=[];connected=[]
    def dns(*args,**kw):
        queries.append(args);return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
    class Socket:
        def __init__(self,*args):pass
        def settimeout(self,v):pass
        def connect(self,target):connected.append(target)
        def close(self):pass
    monkeypatch.setattr(socket,'getaddrinfo',dns);monkeypatch.setattr(socket,'socket',Socket)
    conn=PublicHTTPSConnection('optimum.su')
    conn._create_connection(('optimum.su',443),10)
    assert len(queries)==1 and connected==[('93.184.216.34',443)] and conn.host=='optimum.su'

@pytest.mark.parametrize('value',['²','１２','9'*100,'-1','1.0'])
def test_api_integer_bounds_do_not_raise_server_error(value):
    with pytest.raises(HTTPException):optional_int(value,'id')

def test_page_offset_is_bounded():
    with pytest.raises(ValueError):ProductFilters(page=100001)

def test_nonce_and_hostile_url_are_not_active_links():
    client=TestClient(create_app(store=PanelStore('kami'),auth_required=False))
    r=client.get('/suppliers/1')
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert 'nonce-' in r.headers['content-security-policy'] and r.headers['cache-control']=='no-store'

@pytest.mark.parametrize('target',['http://127.0.0.1/','https://evil.example/catalog/a/'])
def test_optimum_redirect_rejected_before_next_fake_request(target):
    from universal_supplier.adapters.optimum_source.fetch import HttpFetcher
    calls=[]
    async def handler(request):calls.append(str(request.url));return httpx.Response(302,headers={'Location':target})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch=HttpFetcher(client=client,resolve_dns=False)
            with pytest.raises(UnsafeUrlError):await fetch.get('https://optimum.su/catalog/a/')
    asyncio.run(run());assert calls==['https://optimum.su/catalog/a/']

def test_async_dns_pin_rechecks_actual_connection_without_private_io(monkeypatch):
    from universal_supplier.public_connection import public_async_transport
    from httpcore._backends.auto import AutoBackend
    connections=[]
    async def connect(self,host,*args,**kw):connections.append(host);return 'fake-stream'
    monkeypatch.setattr(AutoBackend,'connect_tcp',connect)
    answers=['93.184.216.34']
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(x,443)) for x in answers])
    async def run():
        transport=public_async_transport();backend=transport._pool._network_backend
        assert await backend.connect_tcp('optimum.su',443,10)=='fake-stream'
        answers[:]=['10.0.0.1']
        with pytest.raises(UnsafeUrlError):await backend.connect_tcp('optimum.su',443,10)
        await transport.aclose()
    asyncio.run(run());assert connections==['93.184.216.34']

def test_session_cookie_secure_default_and_local_http_is_explicit(monkeypatch):
    from test_security_review_group5 import _app,PASSWORD
    monkeypatch.delenv('CONTROL_PLANE_INSECURE_HTTP',raising=False)
    app,_=_app();client=TestClient(app,base_url='https://testserver')
    r=client.post('/login',data={'username':'admin1','password':PASSWORD},follow_redirects=False)
    assert r.status_code==303 and 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']

def test_public_health_does_not_disclose_database_or_error():
    from universal_supplier.control_plane.web import public_health
    body,status=public_health({'postgresql':'unavailable','error':'password=secret','database':'private',
                               'version':'PostgreSQL 16','components':{'worker':{'status':'ok','secret':1}}})
    assert status==503 and body=={'application':'error','postgresql':'error','components':{'worker':'ok'},'degraded':False}
    assert 'secret' not in repr(body) and 'private' not in repr(body) and 'PostgreSQL' not in repr(body)

def test_current_panel_workbook_formula_protection_keeps_numbers():
    from universal_supplier.stage6e import xlsx_bytes
    from openpyxl import load_workbook
    from io import BytesIO
    data=xlsx_bytes({'rows':([('value','Value')],[{'value':'=1+1'},{'value':42}])})
    sheet=load_workbook(BytesIO(data)).active
    assert sheet['A2'].value=="'=1+1" and sheet['A2'].data_type=='s'
    assert sheet['A3'].value==42 and sheet['A3'].data_type=='n'
