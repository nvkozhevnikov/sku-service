import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from content_studio.source_reader import SourceReader,source_url,extract_source,read_html
from content_studio.app import create_app
from test_distribution_ai import ai_setup

URL='https://sterbrust.com/catalog/tokarno-vintoreznye-stanki/nastolnyy-tokarnyy-stanok-komtekh-tv-7m/'
HTML=(Path(__file__).parent/'fixtures/product-tv7m-dom.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('bad',[
    'https://evil.example/catalog/a/','https://sterbrust.com.evil.example/catalog/a/',
    'http://sterbrust.com/catalog/a/','https://127.0.0.1/catalog/a/',
    '//sterbrust.com/catalog/a/','https://user:pass@sterbrust.com/catalog/a/',
    'https://sterbrust.com:8888/catalog/a/','https://sterbrust.com/catalog/../bitrix/admin/',
    'https://sterbrust.com/catalog/%2e%2e/bitrix/','https://sterbrust.com/catalog/a/?action=delete',
    'https://sterbrust.com/catalog/a/?token=hidden','https://sterbrust.com/bitrix/admin/',
])
def test_only_safe_own_public_content_paths(bad):
    with pytest.raises(ValueError):source_url(bad)


def test_page_extraction_keeps_real_descriptive_and_spec_context():
    result=extract_source('<nav>UNRELATED_MENU</nav>'+HTML+'<footer>UNRELATED_FOOTER</footer>',URL)
    assert result['title']=='Настольный токарный станок Комтех ТВ-7М'
    assert '275' in result['source'] and 'мм' in result['source'] and '0,75 /380' in result['source']
    assert 'Проточка и расточка' in result['source'] and 'UNRELATED_' not in result['source']
    assert 'В корзину' not in result['source'] and 'Скопировать код' not in result['source']


def test_source_cache_and_provenance_are_read_only_and_explicit_about_edits():
    calls=[];reader=SourceReader(fetch=lambda url:(calls.append(url) or url,HTML))
    first=reader.read(URL);second=reader.read(URL)
    assert first['id']==second['id'] and len(calls)==1
    value=reader.resolve({'source':URL,'channels':['vk']})
    assert value['publication_url']==URL and value['source_import_id']==first['id']
    assert not reader.provenance(value)['edited']
    assert reader.provenance({**value,'source':value['source']+' Изменено.'})['edited']
    assert reader.provenance({**value,'url':'https://sterbrust.com/catalog/other/'})['source_url_changed']
    assert reader.resolve({'source':URL,'publication_url':''})['publication_url']==''
    assert reader.resolve({'source':URL,'publication_url':'https://sterbrust.com/catalog/other/'})['publication_url'].endswith('/other/')


def test_browser_fallback_is_single_bounded_public_read():
    calls=[]
    reader=SourceReader(fetch=lambda url:(url,None),render=lambda url:(calls.append(url) or url,HTML))
    result=reader.read(URL)
    assert calls==[URL] and result['transport']=='browser'


def test_redirect_to_external_host_or_private_dns_is_rejected_before_second_request():
    class Response:
        status=302
        def getheader(self,name,default=''):return 'https://evil.example/catalog/a/'
    with patch('content_studio.source_reader.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('content_studio.source_reader.http.client.HTTPSConnection') as connection:
        connection.return_value.getresponse.return_value=Response()
        with pytest.raises(ValueError):read_html(URL)
        connection.return_value.request.assert_called_once()
        assert 'Authorization' not in connection.return_value.request.call_args.kwargs['headers']
    with patch('content_studio.source_reader.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch('content_studio.source_reader.http.client.HTTPSConnection') as connection:
        with pytest.raises(ValueError):read_html(URL)
        connection.assert_not_called()


def test_fetch_input_and_bad_markup_never_echo_secrets():
    reader=SourceReader(fetch=lambda _:(_ for _ in ()).throw(OSError('SECRET_MUST_NOT_APPEAR')))
    with pytest.raises(ValueError) as error:reader.read(URL)
    assert 'SECRET' not in str(error.value)
    with pytest.raises(ValueError):extract_source('<h1>Only title</h1>',URL)


def test_import_endpoint_auth_and_zero_database_or_ai_mutation(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path);app.state.sources=SourceReader(fetch=lambda url:(url,HTML))
    assert client.post('/api/sources/read',json={'url':URL}).status_code==403
    assert TestClient(app).post('/api/sources/read',json={'url':URL}).status_code==401
    result=client.post('/api/sources/read',json={'url':URL},headers=headers)
    assert result.status_code==200 and result.json()['source_kind']=='product_page'
    assert not app.state.store.list() and not fake.calls


def test_browser_module_input_is_authenticated_bounded_and_not_forwarded_as_raw_html(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    endpoint='/api/sources/from-browser'
    assert client.post(endpoint,json={'url':URL,'html':HTML}).status_code==403
    result=client.post(endpoint,json={'url':URL,'html':HTML},headers=headers)
    assert result.status_code==200 and result.json()['transport']=='browser_module'
    assert '<script' not in result.json()['source'] and not fake.calls and not app.state.store.list()
    assert client.post(endpoint,json={'url':'https://evil.example/catalog/a/','html':HTML},headers=headers).status_code==400
    assert client.post(endpoint,json={'url':URL,'html':'x'*(1024*1024+1)},headers=headers).status_code==400
    info=client.get('/api/sources/browser-module-info').json();assert info['folder'].endswith('browser_import')
    package=client.get('/api/sources/browser-module');assert package.status_code==200 and package.headers['content-type']=='application/zip'


def test_url_only_generates_all_channels_and_links_once_but_manual_removal_sticks(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path);app.state.sources=SourceReader(fetch=lambda url:(url,HTML))
    def reply(config,token,system,user):
        fake.calls.append((config,token,system,user));channels=json.loads(user)['channels']
        return json.dumps({'drafts':{c:{'title':'ТВ-7М','body':'Описание станка' if c=='vk' else '<p>Описание станка.</p>'} for c in channels}})
    fake.request=reply
    state=client.get('/api/ai/gateway').json()
    options={'source':URL,'channels':['vk','news','article'],'engine':'ai','ai_gateway_version':state['version'],'ai_acknowledged':True}
    result=client.post('/api/compose',json=options,headers=headers)
    assert result.status_code==200,result.text
    value=result.json();assert value['project']['drafts']['vk']['body'].count(URL)==1
    assert '275' in value['project']['source']['text'] and value['composition']['imported_source']['url']==URL
    assert all(value['project']['drafts'][c]['body'] for c in options['channels'])
    value=client.post('/api/compose',json={'source':URL,'channels':['vk'],'publication_url':'','publication_link_mode':'manual'},headers=headers).json()
    assert URL not in value['project']['drafts']['vk']['body']


def test_product_source_link_is_allowed_in_generated_news(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path);app.state.sources=SourceReader(fetch=lambda url:(url,HTML))
    fake.reply=json.dumps({'drafts':{'news':{'title':'ТВ-7М','body':'<p>Модель.</p><a href="'+URL+'">Подробнее о станке</a>'}}})
    state=client.get('/api/ai/gateway').json()
    result=client.post('/api/compose',json={'source':URL,'channels':['news'],'engine':'ai','ai_gateway_version':state['version'],'ai_acknowledged':True},headers=headers)
    assert result.status_code==200,result.text
    assert URL in result.json()['project']['drafts']['news']['body']
