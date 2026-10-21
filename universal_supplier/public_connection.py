"""Resolve once, reject non-public answers, connect to the validated literal IP.

TLS still authenticates the original hostname. No global resolver monkeypatch.
Used by real urllib/requests clients; injected offline transports remain offline.
"""
import http.client
import ipaddress
import socket
from urllib.request import HTTPHandler, HTTPSHandler
from .url_safety import _forbidden_address, UnsafeUrlError


def public_socket(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    host,port=address
    infos=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    if not infos or any(_forbidden_address(ipaddress.ip_address(info[4][0])) for info in infos):
        raise UnsafeUrlError('DNS указывает на непубличный адрес')
    last=None
    for family,kind,proto,_,target in infos:
        sock=socket.socket(family,kind,proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:sock.settimeout(timeout)
            if source_address:sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as error:
            last=error;sock.close()
    raise last or OSError('No public address')


class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self._create_connection=public_socket


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self._create_connection=public_socket


class PublicHTTPHandler(HTTPHandler):
    def http_open(self,req):return self.do_open(PublicHTTPConnection,req)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self,req):return self.do_open(PublicHTTPSConnection,req,context=self._context)


def public_requests_adapter():
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    class PublicConnection(HTTPConnection):
        def _new_conn(self):return public_socket((self._dns_host,self.port),self.timeout,self.source_address)
    class PublicTLSConnection(HTTPSConnection):
        def _new_conn(self):return public_socket((self._dns_host,self.port),self.timeout,self.source_address)
    class HTTPPool(HTTPConnectionPool):ConnectionCls=PublicConnection
    class HTTPSPool(HTTPSConnectionPool):ConnectionCls=PublicTLSConnection
    class Adapter(HTTPAdapter):
        def init_poolmanager(self,*args,**kwargs):
            super().init_poolmanager(*args,**kwargs)
            self.poolmanager.pool_classes_by_scheme={'http':HTTPPool,'https':HTTPSPool}
    return Adapter()


def public_async_transport():
    """httpx/httpcore adapter: TLS SNI remains the original origin host."""
    import asyncio
    import ssl
    import httpx
    import httpcore
    from httpcore._backends.auto import AutoBackend
    class PublicBackend(AutoBackend):
        async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
            infos=await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo,host,port,type=socket.SOCK_STREAM),timeout)
            if not infos or any(_forbidden_address(ipaddress.ip_address(info[4][0])) for info in infos):
                raise UnsafeUrlError('DNS указывает на непубличный адрес')
            # The network backend receives a literal. httpcore separately sends
            # start_tls(server_hostname=origin.host), preserving authentication.
            return await super().connect_tcp(infos[0][4][0],port,timeout,local_address,socket_options)
    transport=httpx.AsyncHTTPTransport(trust_env=False)
    transport._pool=httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(),network_backend=PublicBackend())
    return transport
