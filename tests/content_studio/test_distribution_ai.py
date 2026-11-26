import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from content_studio.ai import AIClient, AIService, AIVault, endpoint
from content_studio.app import create_app
from content_studio.composition import generate
from content_studio.distribution import detect_tags, normalize_tags, site_link, apply_footer
from content_studio.exporting import make_export
from content_studio.storage import Store
from test_vk import MemoryVault

SOURCE = (Path(__file__).parent/'fixtures/belmash-b3499-source.html').read_text(encoding='utf-8')
KEY = 'AI_FAKE_SECRET_NEVER_EXTERNAL_123456789'


def test_belmash_tags_specific_explainable_bounded():
    result = detect_tags(SOURCE)
    assert set(result['tags']) == {'Металлообработка', 'BELMASH', 'ТокарныйСтанок', 'Точение', 'BELMASH_B3499'}
    assert all(c['evidence'] for c in result['candidates'])
    assert 'Деревообработка' not in result['tags']


@pytest.mark.parametrize('text,expected,wrong', [
    ('Ленточнопильный станок BELMASH по дереву для мастерской.', ['ЛенточнопильныйСтанок','Деревообработка','Резка'], ['Металлообработка']),
    ('Ленточнопильный станок PROMA для резки металла.', ['ЛенточнопильныйСтанок','Металлообработка'], ['ТокарныйСтанок','Деревообработка']),
    ('Рейсмусовый станок JET для древесины.', ['РейсмусовыйСтанок','Деревообработка','Строгание'], ['Металлообработка']),
    ('Сверлильный станок по металлу.', ['СверлильныйСтанок','Металлообработка'], ['ТокарныйСтанок']),
    ('Компрессор ABAC для сжатого воздуха.', ['Компрессор','ABAC','СжатыйВоздух'], ['Деревообработка','Металлообработка']),
    ('Фрезерный станок по дереву и металлу.', ['ФрезерныйСтанок','Фрезерование'], ['Деревообработка','Металлообработка']),
])
def test_sector_and_type_are_contextual(text, expected, wrong):
    tags = detect_tags(text)['tags']
    assert 2 <= len(tags) <= 5
    assert all(t in tags for t in expected)
    assert not any(t in tags for t in wrong)


def test_unrelated_text_not_padded_with_random_tags():
    result = detect_tags('Сегодня состоялась встреча команды. Обсудили планы.')
    assert result['tags'] == []


@pytest.mark.parametrize('bad', ['#one', '#1 #two', '#one <script>', '#a #bb', '#aa #bb #cc #dd #ee #ff'])
def test_manual_tag_limits(bad):
    with pytest.raises(ValueError): normalize_tags(bad)


def test_manual_tags_deduplicate_and_unicode():
    assert normalize_tags('#BELMASH #belmash, #Резка') == ['BELMASH', 'Резка']


@pytest.mark.parametrize('bad', ['https://sterbrust.ru.evil.example/x','https://evil.example/sterbrust.ru','javascript:alert(1)','https://user:pass@sterbrust.ru/x','https://sterbrust.ru:8888/x','https://sterbrust.ru/?token=secret','https://sterbrust.ru/%0d%0a'])
def test_publication_link_only_own_site(bad):
    with pytest.raises(ValueError): site_link(bad)


def test_footer_round_trip_save_export_no_duplicates(tmp_path):
    app=create_app(tmp_path,'qa');client=TestClient(app);headers={'X-CSRF-Token':client.post('/api/login',json={'code':'qa'}).json()['csrf']}
    r=client.post('/api/compose',json={'source':SOURCE,'channels':['vk'],'publication_url':'sterbrust.ru/catalog/example/','hashtags':['Резка','BELMASH']},headers=headers)
    assert r.status_code==200,r.text
    p=r.json()['project'];tail='https://sterbrust.ru/catalog/example/\n\n#Резка #BELMASH'
    assert p['drafts']['vk']['body'].endswith(tail) and p['drafts']['vk']['body'].count(tail)==1
    for _ in range(2):
        r=client.put('/api/projects/'+p['id'],json={'project':p,'expected_revision':p['revision']},headers=headers);assert r.status_code==200;p=r.json()
    assert p['drafts']['vk']['body'].count(tail)==1
    p['drafts']['vk']['vk_options']={'publication_url':'/news/example/','hashtags':['Точение','BELMASH']}
    r=client.put('/api/projects/'+p['id'],json={'project':p,'expected_revision':p['revision']},headers=headers);assert r.status_code==200;p=r.json()
    assert '/catalog/example/' not in p['drafts']['vk']['body']
    assert p['drafts']['vk']['body'].endswith('https://sterbrust.com/news/example/\n\n#Точение #BELMASH')
    with zipfile.ZipFile(io.BytesIO(make_export(app.state.store,p))) as z:
        assert z.read('vk/post.txt').decode()==p['drafts']['vk']['body']
        assert json.loads(z.read('project.json'))['project']['drafts']['vk']['vk_options']==p['drafts']['vk']['vk_options']


@pytest.mark.parametrize('bad', ['http://api.example.com/v1','https://localhost/v1','https://10.0.0.1/v1','https://127.0.0.1/v1','https://api.example.com/v1?key=secret','https://user:key@api.example.com/v1','https://api.example.com/v1/chat/completions'])
def test_ai_endpoint_bounds(bad):
    with pytest.raises(ValueError): endpoint(bad)


class FakeAI:
    def __init__(self):self.calls=[];self.reply=None
    def request(self, config, token, system, user):
        self.calls.append((config,token,system,user))
        if self.reply is not None:return self.reply
        return json.dumps({'drafts':{'vk':{'title':'BELMASH B3499','body':'Потребляемая мощность:600Вт. Подробности взяты из исходника.'}}})


def ai_setup(tmp_path):
    app=create_app(tmp_path,'qa');fake=FakeAI();app.state.ai=AIService(app.state.store,fake,MemoryVault());client=TestClient(app);headers={'X-CSRF-Token':client.post('/api/login',json={'code':'qa'}).json()['csrf']}
    cfg={'base_url':'https://gateway.example.com/v1','api_type':'openai','model':'chinese-vendor/custom-model','key':KEY,'token_limit':4096}
    response=client.post('/api/ai/settings',json=cfg,headers=headers);assert response.status_code==200
    return app,fake,client,headers,response.json()


def test_ai_settings_private_explicit_and_no_network_on_save(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    assert fake.calls==[] and 'key_ref' not in cfg and KEY not in json.dumps(cfg)
    assert client.post('/api/ai/settings',json={}).status_code==403
    assert KEY not in client.get('/api/ai/settings').text
    assert client.post('/api/ai/test',json={'version':cfg['version']},headers=headers).status_code==400
    changed=client.post('/api/ai/settings',json={'base_url':'https://other.example.com','api_type':'openai','model':'same','key':''},headers=headers)
    assert changed.status_code==400 and fake.calls==[]


def test_ai_generation_selected_drafts_footer_and_unreviewed(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    v={'source':SOURCE,'channels':['vk'],'engine':'ai','ai_version':cfg['version'],'ai_acknowledged':True,'reviewed':True,'publication_url':'https://sterbrust.ru/news/example/'}
    assert client.post('/api/compose',json={**v,'ai_acknowledged':False},headers=headers).status_code==400 and not fake.calls
    r=client.post('/api/compose',json=v,headers=headers);assert r.status_code==200,r.text
    p=r.json()['project'];assert p['drafts']['vk']['state']=='DRAFT' and p['facts'][0]['state']=='UNREVIEWED'
    assert not p['drafts']['news']['body'] and p['drafts']['vk']['body'].count('https://sterbrust.ru/news/example/')==1
    assert p['drafts']['vk']['body'].splitlines()[-1].startswith('#')
    assert r.json()['composition']['engine']=='ai'
    assert len(fake.calls)==1 and KEY not in fake.calls[0][3]
    with zipfile.ZipFile(io.BytesIO(make_export(app.state.store,p,'backup'))) as z:
        assert all(KEY.encode() not in z.read(name) for name in z.namelist())


@pytest.mark.parametrize('reply', ['bad json', '{"drafts":{}}', '{"drafts":{"vk":{"body":"<script>bad()</script>"},"article":{}}}', '{"drafts":{"vk":{"body":"https://evil.example"}}}'])
def test_bad_ai_reply_does_not_create_or_replace_project(tmp_path,reply):
    app,fake,client,headers,cfg=ai_setup(tmp_path);fake.reply=reply;before=app.state.store.list()
    r=client.post('/api/compose',json={'source':SOURCE,'channels':['vk'],'engine':'ai','ai_version':cfg['version'],'ai_acknowledged':True},headers=headers)
    assert r.status_code==400 and app.state.store.list()==before and len(fake.calls)==1


def test_ai_transport_rejects_private_dns_before_key_is_sent():
    cfg={'base_url':'https://api.example.com/v1','model':'test','api_type':'openai','token_limit':256,'token_parameter':'max_tokens','json_mode':True}
    with patch('content_studio.ai.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch('content_studio.ai.http.client.HTTPSConnection') as connect:
        with pytest.raises(ValueError):AIClient().request(cfg,KEY,'test','test')
        connect.assert_not_called()


@pytest.mark.parametrize('kind',['openai','anthropic'])
def test_ai_transport_formats_and_bounds_without_real_network(kind):
    cfg={'base_url':'https://api.example.com/v1','model':'example/model','api_type':kind,'token_limit':4096,'token_parameter':'max_completion_tokens','json_mode':True}
    reply={'stop_reason':'end_turn','content':[{'type':'text','text':'{"ok":true}'}]} if kind=='anthropic' else {'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}
    class Response:
        status=200
        def read(self,limit):return json.dumps(reply).encode()
    with patch('content_studio.ai.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('content_studio.ai.http.client.HTTPSConnection') as connect:
        connection=connect.return_value;connection.getresponse.return_value=Response()
        assert AIClient().request(cfg,KEY,'JSON system','user text')=='{"ok":true}'
        args=connection.request.call_args
        payload=json.loads(args.kwargs['body']);headers=args.kwargs['headers']
        assert KEY not in json.dumps(payload) and payload['model']=='example/model'
        assert args.args[1]==('/v1/messages' if kind=='anthropic' else '/v1/chat/completions')
        if kind=='anthropic':assert headers['x-api-key']==KEY and headers['anthropic-version']=='2026-10-21' and payload['max_tokens']==4096
        else:assert headers['Authorization']=='Bearer '+KEY and payload['max_completion_tokens']==4096 and payload['response_format']=={'type':'json_object'}
        connection.close.assert_called_once()


def test_ai_redirect_never_follows_or_retries_or_echoes_provider_error():
    cfg={'base_url':'https://api.example.com','model':'example','api_type':'openai','token_limit':256,'token_parameter':'max_tokens','json_mode':True}
    class Response:
        status=307
        def read(self,limit):return ('redirect '+KEY).encode()
    with patch('content_studio.ai.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('content_studio.ai.http.client.HTTPSConnection') as connect:
        connect.return_value.getresponse.return_value=Response()
        with pytest.raises(ValueError) as error:AIClient().request(cfg,KEY,'system','user')
        assert KEY not in str(error.value) and 'HTTP 307' in str(error.value)
        connect.return_value.request.assert_called_once()


def test_ai_key_encrypted_with_real_windows_dpapi(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Windows DPAPI only')
    vault=AIVault(tmp_path)
    ref=vault.put(KEY)
    assert vault.get(ref)==KEY
    assert KEY.encode() not in (tmp_path/(ref+'.dpapi')).read_bytes()
