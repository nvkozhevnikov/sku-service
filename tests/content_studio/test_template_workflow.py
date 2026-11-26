import io
import json
import zipfile
from pathlib import Path

import pytest
from lxml import html
from fastapi.testclient import TestClient

from content_studio.app import create_app
from content_studio.content import sanitize, safe_news_link
from content_studio.editorial_style import natural_text, natural_html
from content_studio.exporting import make_export
from content_studio.vk_setup import user_token_input
from test_distribution_ai import ai_setup
from test_vk import FakeVk, MemoryVault, VkService, SECRET, ready
from content_studio.vk_transport import VkError
from content_studio.distribution import detect_tags

SOURCE=(Path(__file__).parent/'fixtures/komtekh-ready-news.html').read_text(encoding='utf-8')


def test_exact_ready_layout_survives_compose_save_export(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    r=client.post('/api/compose',json={'source':SOURCE,'channels':['news'],'news_mode':'auto','engine':'ai'},headers=headers)
    assert r.status_code==200,r.text
    p=r.json()['project'];body=p['drafts']['news']['body'];tree=html.fragment_fromstring(body,create_parent='div')
    assert not fake.calls and r.json()['composition']['preserved_news']
    assert len(tree.xpath('.//section'))==3 and len(tree.xpath('.//a[contains(@href,"/catalog/")]'))==14
    for style in ['display:flex','display:grid','grid-template-columns:minmax(0,1fr)','background:#f7f8f9','color:#ffffff!important','padding:23px 25px']:
        assert style in body
    assert 'sterbrust-komtekh-prices' in body and 'mailto:post@sterbrust.com' in body and 'tel:88005058147' in body
    assert p['source']['text']==SOURCE and p['drafts']['news']['state']=='DRAFT'
    assert 'повышаются цены' in p['facts'][0]['text']
    assert p['drafts']['news']['title']=='Изменение прайс-листа'
    saved=client.put('/api/projects/'+p['id'],json={'project':p,'expected_revision':p['revision']},headers=headers).json()
    assert saved['drafts']['news']['body']==body
    with zipfile.ZipFile(io.BytesIO(make_export(app.state.store,saved,'transfer'))) as z:
        assert any(b'display:flex' in z.read(n) and b'grid-template-columns' in z.read(n) for n in z.namelist() if n.endswith('.html'))


def test_preserve_news_routes_only_vk_to_model(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    r=client.post('/api/compose',json={'source':SOURCE,'channels':['news','vk'],'news_mode':'auto',
        'engine':'ai','ai_version':cfg['version'],'ai_acknowledged':True},headers=headers)
    assert r.status_code==200,r.text
    assert len(fake.calls)==1 and json.loads(fake.calls[0][3])['channels']==['vk']
    assert 'display:flex' in r.json()['project']['drafts']['news']['body']
    assert 'конкретное событие' in fake.calls[0][2].lower()


@pytest.mark.parametrize('source', ['<p onclick="danger()">Text</p><script>danger()</script>',
    '<div style="background:url(https://evil.example/x);position:fixed;display:flex">Text</div>',
    '<a href="mailto:post@sterbrust.com?body=secret">Bad</a><iframe src="http://127.0.0.1/">bad</iframe>'])
def test_ready_layout_remains_sanitized(source):
    safe,_=sanitize(source,profile='news')
    assert not any(x in safe for x in ('onclick','<script','url(','position:fixed','?body=','<iframe'))


def test_ready_html_does_not_reveal_hidden_source_content(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    source='<div><p>Публичная новость.</p><p hidden>HIDDEN_ONE</p><span aria-hidden="true">HIDDEN_TWO</span><div style="display:none!important">HIDDEN_THREE</div></div>'
    result=client.post('/api/compose',json={'source':source,'channels':['news'],'news_mode':'auto'},headers=headers)
    assert result.status_code==200
    body=result.json()['project']['drafts']['news']['body']
    assert 'HIDDEN_' not in body and 'Публичная новость' in body


def test_contacts_only_public_literal_addresses():
    assert safe_news_link('tel:88005058147') and safe_news_link('mailto:post@sterbrust.com')
    assert not safe_news_link('mailto:evil@example.com') and not safe_news_link('mailto:post@sterbrust.com?subject=bad')


def test_natural_prose_does_not_touch_model_urls_time_or_css():
    source='Комтех ТВ-7М — обновление: 600 Вт. До 12:30. https://sterbrust.com/catalog/a/?x=1:2'
    result=natural_text(source)
    assert '—' not in result and 'обновление 600 Вт' in result
    assert 'ТВ-7М' in result and '12:30' in result and 'https://sterbrust.com/catalog/a/?x=1:2' in result
    assert natural_text('Соотношение 1:2, время 12:30:45')=='Соотношение 1:2, время 12:30:45'
    result=natural_html('<p style="color:#f2722c">Новость: <a href="https://sterbrust.com/catalog/a/">ТВ-7М — модель</a></p>')
    assert 'color:#f2722c' in result and 'https://sterbrust.com/catalog/a/' in result and '—' not in result


def test_komtekh_tags_recognize_shared_machine_list_without_accessory_confusion():
    tags=detect_tags(SOURCE)['tags']
    assert set(['Комтех','ТокарныйСтанок','СверлильныйСтанок','ЗаточнойСтанок']).issubset(tags)
    assert 'Оснастка' not in tags and len(tags)<=5


def test_natural_rule_can_be_disabled_in_panel(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    state=client.get('/api/ai/gateway').json()
    state=client.post('/api/ai/gateway/rules-import',json={'version':state['version'],'channel':'vk'},headers=headers).json()
    fake.reply=json.dumps({'drafts':{'vk':{'title':'Модель','body':'Комтех ТВ-7М — модель: 600 Вт.'}}})
    options={'source':SOURCE,'channels':['vk'],'engine':'ai','ai_acknowledged':True,'ai_gateway_version':state['version']}
    result=client.post('/api/compose',json=options,headers=headers)
    assert result.status_code==200
    assert '—' not in result.json()['project']['drafts']['vk']['body'] and 'модель 600 Вт' in result.json()['project']['drafts']['vk']['body']
    rule=next(r for r in state['editorial_rules']['vk'] if r['code'].endswith('.natural_style'))
    state=client.post('/api/ai/gateway/rules-save',json={'version':state['version'],'channel':'vk','id':rule['id'],'title':rule['title'],'text':rule['text'],'enabled':False},headers=headers).json()
    result=client.post('/api/compose',json={**options,'ai_gateway_version':state['version']},headers=headers)
    assert result.status_code==200 and '— модель:' in result.json()['project']['drafts']['vk']['body']


def test_standalone_preview_has_all_inline_styles_csp_and_auth(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    p=client.post('/api/compose',json={'source':SOURCE,'channels':['news'],'news_mode':'auto'},headers=headers).json()['project']
    path='/api/projects/'+p['id']+'/preview-window/news'
    response=client.get(path)
    assert response.status_code==200 and 'display:flex' in response.text and 'grid-template-columns' in response.text
    assert 'script-src \'none\'' in response.headers['Content-Security-Policy']
    assert 'unsafe-hashes' in response.headers['Content-Security-Policy']
    assert TestClient(app).get(path).status_code==401
    assert 'https://sterbrust.com/catalog/' in response.text


def test_user_oauth_result_parsed_locally_without_echo():
    assert user_token_input('https://oauth.vk.com/blank.html#access_token='+SECRET+'&expires_in=0')==SECRET
    for value in ['https://evil.example/#access_token='+SECRET,'https://oauth.vk.com/blank.html?access_token='+SECRET,'https://oauth.vk.com/blank.html#access_token=a&access_token=b']:
        with pytest.raises(ValueError) as e:user_token_input(value)
        assert SECRET not in str(e.value)


def test_vk_api_user_connect_checks_photos_without_upload_or_post(tmp_path):
    app=create_app(tmp_path,'qa');service=app.state.vk;service.client=FakeVk();service.vault=MemoryVault()
    client=TestClient(app);headers={'X-CSRF-Token':client.post('/api/login',json={'code':'qa'}).json()['csrf']}
    result=client.post('/api/vk/connect',json={'kind':'user','token':'https://oauth.vk.com/blank.html#access_token='+SECRET},headers=headers)
    assert result.status_code==200 and result.json()['photo_setup']['ready']
    assert [name for name,_ in service.client.calls]==['account.getAppPermissions','groups.getById','photos.getWallUploadServer']
    assert not service.client.uploads and SECRET not in result.text


def test_missing_user_key_is_honest_and_photo_delivery_is_automatic_after_connect(tmp_path):
    app=create_app(tmp_path,'qa');service=app.state.vk;service.client=FakeVk();service.vault=MemoryVault()
    service.connect(SECRET,'community');service.client.photo_error=VkError(27);service.probe_photos()
    assert service.status()['photo_setup']['reason']=='user_key_required'
    p=ready(app.state.store,2)
    with pytest.raises(ValueError):service.prepare(p['id'])
    service.client.photo_error=None;service.connect(SECRET,'user');service.probe_photos()
    intent=service.prepare(p['id']);assert service.claim(intent['id'],intent['digest'],True)
    service.dispatch(intent['id'])
    assert service.get(intent['id'])['state']=='APPLIED' and service.client.uploads==2
    assert len(service.client.attachments)==2
