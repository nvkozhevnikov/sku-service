"""Intervesp root-store regression. No HTTP, TLS network or SQL."""
from hashlib import sha256
from pathlib import Path
import ssl
import certifi
import pytest
from universal_supplier import public_connection as pc

ROOT_2022='8faf7d2e2cb4709bb8e0b33666bf75a5dd45b5de480f8ea8d4bfe6bebc17f2ed'

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a,**k):raise AssertionError('OFFLINE_NO_NETWORK_OR_SQL')
    monkeypatch.setattr('socket.create_connection',denied)
    monkeypatch.setattr('urllib.request.OpenerDirector.open',denied)
    monkeypatch.setattr('psycopg.connect',denied)


def test_exact_root_from_actual_failed_intervesp_chain_is_in_verified_bundle():
    conn=pc.PublicHTTPSConnection('intervesp.ru')
    assert conn._context.verify_mode==ssl.CERT_REQUIRED and conn._context.check_hostname
    roots={sha256(der).hexdigest() for der in conn._context.get_ca_certs(binary_form=True)}
    assert ROOT_2022 in roots


def test_context_uses_explicit_installed_certifi_not_environment_or_peer_ca(monkeypatch,tmp_path):
    seen=[];original=ssl.create_default_context
    monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'untrusted-environment.pem'))
    def context(*a,**kw):seen.append(kw);return original(*a,**kw)
    monkeypatch.setattr(ssl,'create_default_context',context)
    assert pc.public_tls_context().check_hostname
    assert seen==[{'cafile':certifi.where()}]
    identity=pc.public_tls_identity()
    assert identity['ca_bundle_sha256']==sha256(Path(certifi.where()).read_bytes()).hexdigest()
    assert identity['verify_mode']=='CERT_REQUIRED' and identity['check_hostname'] is True


def test_missing_bundle_fails_closed_before_tcp(monkeypatch,tmp_path):
    monkeypatch.setattr(certifi,'where',lambda:str(tmp_path/'absent.pem'))
    with pytest.raises(FileNotFoundError):pc.PublicHTTPSConnection('intervesp.ru')


@pytest.mark.parametrize('hostname_check',[False,True])
def test_unverified_or_no_hostname_context_is_not_accepted(hostname_check):
    context=ssl._create_unverified_context()
    if hostname_check:context.check_hostname=True
    # check_hostname=True switches verify_mode to REQUIRED; explicitly disable
    # hostname again for the independent bad-hostname case.
    if hostname_check:context.check_hostname=False
    with pytest.raises(ValueError,match='PUBLIC_TLS_VERIFICATION_REQUIRED'):
        pc.PublicHTTPSConnection('intervesp.ru',context=context)


def test_sni_remains_original_host_with_same_public_socket_contract(monkeypatch):
    events=[]
    class Sock:
        def setsockopt(self,*a):pass
    class Context:
        verify_mode=ssl.CERT_REQUIRED;check_hostname=True
        def set_alpn_protocols(self,p):events.append(('alpn',p))
        def wrap_socket(self,s,*,server_hostname):events.append(('SNI',server_hostname));return s
    monkeypatch.setattr(pc,'public_socket',lambda address,*a,**k:(events.append(('public_socket',address)) or Sock()))
    conn=pc.PublicHTTPSConnection('intervesp.ru',context=Context());conn.connect()
    assert ('public_socket',('intervesp.ru',443)) in events and ('SNI','intervesp.ru') in events


def test_async_public_transport_uses_same_verified_context():
    import asyncio
    transport=pc.public_async_transport()
    context=transport._pool._ssl_context
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert ROOT_2022 in {sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
    asyncio.run(transport.aclose())
