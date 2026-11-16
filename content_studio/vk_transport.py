"""Bounded VK HTTPS client: fixed API, pinned public upload address, no retries."""
from __future__ import annotations

import ctypes
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import certifi


class VkError(Exception):
    def __init__(self, code=None, uncertain=False, stage='api'):
        self.code=code;self.uncertain=uncertain;self.stage=stage
        super().__init__('VK отклонил операцию.' if code is not None else 'Нет однозначного ответа VK.')


class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]


class WindowsVault:
    """DPAPI current-user encryption; nothing is stored in browser/export/git."""
    def __init__(self,root):self.root=Path(root);self.root.mkdir(exist_ok=True)

    def _crypt(self,value,decrypt=False):
        if os.name!='nt':raise ValueError('Хранение ключей этой версии требует Windows DPAPI.')
        buffer=ctypes.create_string_buffer(value);source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));dest=Blob()
        crypt=ctypes.windll.crypt32;free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        ok=fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(dest))
        if not ok:raise ValueError('Не удалось открыть защищённое хранилище Windows.')
        try:return ctypes.string_at(dest.data,dest.size)
        finally:free(ctypes.cast(dest.data,ctypes.c_void_p))

    def put(self,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9._-]{30,4096}',token):raise ValueError('Некорректный формат ключа VK.')
        ref=uuid.uuid4().hex
        with (self.root/(ref+'.dpapi')).open('xb') as f:
            f.write(self._crypt(token.encode()));f.flush();os.fsync(f.fileno())
        return ref

    def get(self,ref):
        if not re.fullmatch('[0-9a-f]{32}',ref):raise ValueError('Некорректная ссылка на ключ.')
        return self._crypt((self.root/(ref+'.dpapi')).read_bytes(),True).decode()


class VkClient:
    VERSION='5.199'
    METHODS={'groups.getById','groups.getTokenPermissions','account.getAppPermissions','photos.getWallUploadServer',
             'photos.saveWallPhoto','wall.post','wall.getById'}

    def _request(self,host,path,body,headers,write=False,pinned=None):
        context=ssl.create_default_context(cafile=certifi.where())
        conn=http.client.HTTPSConnection(host,443,timeout=20,context=context)
        if pinned:
            conn._create_connection=lambda address,timeout,source_address=None:socket.create_connection((pinned,443),timeout,source_address)
        try:
            conn.request('POST',path,body=body,headers=headers)
            response=conn.getresponse();raw=response.read(2*1024*1024+1)
            if response.status!=200 or len(raw)>2*1024*1024:raise VkError(uncertain=write)
            try:result=json.loads(raw)
            except (ValueError,UnicodeDecodeError):raise VkError(uncertain=write) from None
            if not isinstance(result,dict):raise VkError(uncertain=write)
            return result
        except VkError:raise
        except Exception:raise VkError(uncertain=write) from None
        finally:conn.close()

    def call(self,method,token,params=None):
        if method not in self.METHODS:raise ValueError('Метод VK не разрешён модулем.')
        data={**(params or {}),'v':self.VERSION,'access_token':token}
        result=self._request('api.vk.com','/method/'+method,urlencode(data).encode(),{'Content-Type':'application/x-www-form-urlencoded'},method in ('wall.post','photos.saveWallPhoto'))
        if 'error' in result:
            code=result['error'].get('error_code') if isinstance(result['error'],dict) else None
            raise VkError(code if type(code) is int else None,uncertain=code is None,stage=method)
        if 'response' not in result:raise VkError(uncertain=method in ('wall.post','photos.saveWallPhoto'))
        return result['response']

    def upload(self,url,jpeg):
        try:
            p=urlsplit(url);port=p.port
        except ValueError:raise ValueError('Некорректный адрес загрузки VK.') from None
        host=p.hostname or ''
        if p.scheme!='https' or port not in (None,443) or p.username or p.password or p.fragment or not (host in ('pu.vk.com','pu.vk.ru') or re.fullmatch(r'[a-z0-9-]+\.userapi\.com',host)):
            raise ValueError('Адрес загрузки не входит в разрешённый список VK.')
        try:addresses={x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
        except OSError:raise VkError(stage='upload') from None
        if not addresses or not all(ipaddress.ip_address(x).is_global for x in addresses):raise ValueError('Запрещённый IP сервера загрузки.')
        boundary='Studio'+uuid.uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="photo.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+jpeg+f'\r\n--{boundary}--\r\n'.encode())
        # No token or cookies are forwarded to the upload host. Redirects are never followed.
        result=self._request(host,p.path+('?' + p.query if p.query else ''),body,{'Content-Type':f'multipart/form-data; boundary={boundary}'},True,sorted(addresses)[0])
        if not all(k in result for k in ('server','photo','hash')):raise VkError(uncertain=True,stage='upload')
        return {k:result[k] for k in ('server','photo','hash')}
