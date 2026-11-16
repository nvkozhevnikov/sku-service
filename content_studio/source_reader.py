"""Bounded read-only import of public Sterbrust product/news/article pages."""
import hashlib
import http.client
import ipaddress
import re
import secrets
import socket
import ssl
import time
from datetime import datetime,timezone
from threading import Lock
from urllib.parse import urljoin,urlsplit,urlunsplit

import certifi
from lxml import html

from .owned_sites import OWNED_HOSTS,CANONICAL_SITE
from .source_parser import parse_source

MAX_HTML=6*1024*1024


def source_url(value):
    if not isinstance(value,str) or not value.strip() or len(value)>3000:raise ValueError('Вставьте ссылку на карточку товара Sterbrust.')
    value=value.strip()
    if value.startswith('/') and not value.startswith('//'):value=CANONICAL_SITE+value
    elif value.split('/',1)[0].lower() in OWNED_HOSTS:value='https://'+value
    if re.search(r'[\s\\\x00-\x1f]|%[01][0-9a-f]|%25|%2e|%2f|%5c|%3f|%23',value,re.I):raise ValueError('Некорректный адрес страницы.')
    try:
        p=urlsplit(value)
        good=p.scheme=='https' and p.hostname in OWNED_HOSTS and p.port in (None,443) and not any((p.username,p.password,p.query))
        good=good and not any(part in ('.','..') for part in p.path.split('/'))
        good=good and any(p.path.startswith(prefix) for prefix in ('/catalog/','/company/news/','/news/','/articles/'))
        if not good:raise ValueError()
    except ValueError:raise ValueError('Для импорта нужна HTTPS-ссылка на товар, новость или статью Sterbrust без параметров доступа.') from None
    return urlunsplit(('https',p.hostname,p.path,'',''))


def only_source_link(text):
    if not isinstance(text,str):return False
    return bool(re.fullmatch(r'(?:https?://|(?:www\.)?sterbrust\.(?:com|ru)/)[^\s<>]+',text.strip(),re.I))


def read_html(url):
    current=source_url(url)
    for _ in range(3):
        p=urlsplit(current)
        addresses={r[4][0] for r in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)}
        if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):raise ValueError('Сайт разрешился в непубличный адрес. Импорт остановлен.')
        connection=http.client.HTTPSConnection(p.hostname,443,timeout=12,context=ssl.create_default_context(cafile=certifi.where()))
        connection._create_connection=lambda address,timeout,source_address=None:socket.create_connection((sorted(addresses)[0],443),timeout,source_address)
        try:
            connection.request('GET',p.path,headers={'Accept':'text/html','Accept-Language':'ru-RU,ru;q=0.9','Accept-Encoding':'identity','User-Agent':'Mozilla/5.0'})
            response=connection.getresponse()
            if response.status in (301,302,303,307,308):
                current=source_url(urljoin(current,response.getheader('Location','')));continue
            if response.status in (401,403):return current,None
            if response.status!=200:raise ValueError('Страница не прочитана. HTTP '+str(response.status)+'. Данные и ключи ИИ не изменены.')
            if 'text/html' not in response.getheader('Content-Type','').lower():raise ValueError('По ссылке нет HTML-страницы.')
            raw=response.read(MAX_HTML+1)
            if len(raw)>MAX_HTML:raise ValueError('Страница больше 6 MiB.')
            if response.getheader('Content-Encoding','identity') not in ('','identity'):return current,None
            return current,raw.decode('utf-8')
        finally:connection.close()
    raise ValueError('Слишком много перенаправлений страницы.')


def extract_source(raw,url):
    if not isinstance(raw,str) or len(raw.encode())>MAX_HTML:raise ValueError('Страница больше допустимого размера.')
    tree=html.fromstring(raw,parser=html.HTMLParser(no_network=True))
    headings=tree.xpath('//h1')
    if not headings:raise ValueError('Не найден заголовок материала. Вставьте текст или HTML вручную.')
    title=' '.join(headings[0].text_content().split())[:250]
    if not title:raise ValueError('Заголовок материала пуст.')
    regions=tree.xpath('//*[@id="desc"]')
    if not regions:regions=tree.xpath('//*[@itemprop="description"] | //*[contains(concat(" ",normalize-space(@class)," ")," detail_text ")]')
    if not regions and '/catalog/' not in urlsplit(url).path:regions=tree.xpath('//article | //main')
    if not regions:raise ValueError('Не найдено описание карточки. Вставьте описание или HTML вручную.')
    markup=html.tostring(headings[0],encoding='unicode',method='html')+html.tostring(regions[0],encoding='unicode',method='html')
    parsed=parse_source(markup)
    blocks=[]
    for block in parsed.blocks:
        text=block.text.strip()
        if text and text not in blocks:blocks.append(text)
    source='\n\n'.join(blocks)
    if len(source)>38000:raise ValueError('Описание длиннее 38 000 знаков. Вставьте и сократите нужную часть вручную.')
    if len(source)<len(title)+20:raise ValueError('В карточке недостаточно содержательного текста.')
    return {'title':title,'source':source,'url':url,'source_kind':'product_page' if '/catalog/' in urlsplit(url).path else 'site_page'}


class SourceReader:
    def __init__(self,fetch=None,render=None):
        self.fetch=fetch or read_html
        if render is None:
            def browser_required(url):
                raise ValueError('Защита сайта не отдала страницу обычному запросу. Подключите браузерный импорт в Studio или вставьте текст вручную. Данные и ключи не изменены.')
            render=browser_required
        self.render=render;self.lock=Lock();self.cache={}

    def read(self,value):
        url=source_url(value)
        for item in list(self.cache.values()):
            if item['url']==url and time.monotonic()<item['_expires']:return self.public(item)
        if not self.lock.acquire(blocking=False):raise ValueError('Уже читается другая страница. Дождитесь завершения.')
        try:
            final,raw=self.fetch(url);transport='http'
            if raw is None:final,raw=self.render(final);transport='browser'
            final=source_url(final)
            result=extract_source(raw,final)
            result.update(id=secrets.token_hex(16),fetched_at=datetime.now(timezone.utc).isoformat(),transport=transport,
                source_hash=hashlib.sha256(result['source'].encode()).hexdigest(),_expires=time.monotonic()+300)
            while len(self.cache)>=10:self.cache.pop(next(iter(self.cache)))
            self.cache[result['id']]=result
            return self.public(result)
        except (ValueError,):raise
        except Exception:raise ValueError('Не удалось прочитать страницу. Исходник сохранён; можно вставить текст вручную.') from None
        finally:self.lock.release()

    @staticmethod
    def public(item):return {k:v for k,v in item.items() if not k.startswith('_')}

    def from_browser(self,value):
        url=source_url(value.get('url'))
        raw=value.get('html')
        if not isinstance(raw,str) or len(raw.encode())>1024*1024:raise ValueError('Описание из браузера слишком большое.')
        item=extract_source(raw,url)
        item.update(id=secrets.token_hex(16),fetched_at=datetime.now(timezone.utc).isoformat(),transport='browser_module',
            source_hash=hashlib.sha256(item['source'].encode()).hexdigest(),_expires=time.monotonic()+300)
        while len(self.cache)>=10:self.cache.pop(next(iter(self.cache)))
        self.cache[item['id']]=item
        return self.public(item)

    def resolve(self,value):
        v=dict(value)
        if not isinstance(v.get('source',''),str):return v
        if only_source_link(v.get('source','')) or (not v.get('source','').strip() and v.get('url')):
            item=self.read(v.get('source','').strip() or v['url'])
            v.update(source=item['source'],url=item['url'],source_kind=item['source_kind'],source_import_id=item['id'])
            if not v.get('title'):v['title']=item['title']
            if 'publication_url' not in v:v['publication_url']=item['url']
        return v

    def provenance(self,value):
        item=self.cache.get(value.get('source_import_id'))
        if not item:return None
        return {k:item[k] for k in ('url','fetched_at','transport','source_kind','source_hash')} | {
            'edited':hashlib.sha256(value.get('source','').encode()).hexdigest()!=item['source_hash'],
            'source_url_changed':value.get('url','')!=item['url']}
