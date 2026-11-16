import io,json,time,zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from content_studio.app import create_app
from content_studio.composition import generate
from content_studio.vk_transport import VkError
from test_vk import setup,ready,SECRET


SOURCE='Новая серия оборудования\n\nПроизводитель представил новую модель станка.\n\nВ комплект входят руководство и защитный экран.\n\nПоставки начнутся после подтверждения заказа.'


@pytest.mark.parametrize('channels',[['vk'],['news'],['article'],['vk','news','article']])
def test_selected_channels_only_no_approved(channels):
    result=generate(SOURCE,channels=channels,preset='product')
    assert set(result['drafts'])==set(channels)
    assert all(d['state']=='DRAFT' for d in result['drafts'].values())
    assert result['facts'][0]['state']=='UNREVIEWED'
    assert result['source']['text']==SOURCE
    if 'article' in channels:assert '<h2>Что известно' in result['drafts']['article']['body']
    if 'vk' in channels:assert '<p>' not in result['drafts']['vk']['body']


def test_no_invented_values_and_source_instruction_inert():
    source='<h2>Предложение</h2><p>Скидка 7% до 15 ноября.</p><script>publish_everything()</script>'
    p=generate(source,channels=['vk','news','article'],preset='promotion',reviewed=True)
    assert p['facts'][0]['state']=='CONFIRMED'
    for d in p['drafts'].values():assert '7%' in d['body'] and 'publish_everything' not in d['body']


@pytest.mark.parametrize('channels',[[],['other'],['vk','vk'],[{}]])
def test_invalid_channels(channels):
    with pytest.raises(ValueError):generate(SOURCE,channels=channels)


def test_compose_route_atomic_selection_and_recompose_other_unchanged(tmp_path):
    app=create_app(tmp_path,'code');client=TestClient(app);token=client.post('/api/login',json={'code':'code'}).json()['csrf'];headers={'X-CSRF-Token':token}
    request={'source':SOURCE,'channels':['vk','news'],'preset':'product','reviewed':True}
    assert client.post('/api/compose',json=request).status_code==403
    response=client.post('/api/compose',json=request,headers=headers);assert response.status_code==200
    p=response.json()['project'];assert p['drafts']['article']['body']=='' and p['drafts']['vk']['body']
    meta=app.state.store.composition(p['id']);assert meta['engine']=='local-source-template'
    oldvk=deepcopy(p['drafts']['vk']);request.update(channels=['article'],expected_revision=p['revision'])
    r=client.post('/api/projects/'+p['id']+'/compose',json=request,headers=headers);assert r.status_code==200
    p=r.json()['project'];assert p['drafts']['vk']['body']==oldvk['body'] and p['drafts']['article']['body']
    assert client.post('/api/projects/'+p['id']+'/compose',json=request,headers=headers).status_code==409
    before=app.state.store.list()
    assert client.post('/api/compose',json={**request,'source':' '},headers=headers).status_code==400
    assert app.state.store.list()==before


def test_schedule_with_blocked_photos_retains_files_and_sends_no_upload(setup):
    store,client,vault,s=setup;client.photo_error=VkError(27);s.probe_photos();p=ready(store,2)
    date=int(time.time())+86400;intent=s.prepare(p['id'],'publish_with_fallback',date)
    assert intent['delivery_plan']=='scheduled_text' and len(intent['image_ids'])==2
    assert not any(m=='wall.post' for m,_ in client.calls)
    s.claim(intent['id'],intent['digest'],True);s.dispatch(intent['id']);result=s.get(intent['id'])
    assert result['state']=='APPLIED' and result['result']['delivery']=='scheduled_text' and result['result']['verification']=='VERIFIED'
    posts=[params for method,params in client.calls if method=='wall.post'];assert len(posts)==1 and posts[0]['publish_date']==date and posts[0]['attachments']==''
    assert client.uploads==0
    assert len(store.get(p['id'])['drafts']['vk']['media'])==2
    with zipfile.ZipFile(io.BytesIO(s.media_package(intent['id']))) as z:
        assert '01.jpg' in z.namelist() and '02.jpg' in z.namelist()
        assert Image.open(io.BytesIO(z.read('01.jpg'))).size==(1200,1200)
        assert SECRET.encode() not in z.read('manifest.json')


def test_schedule_failure_never_claims_postponed(setup):
    store,client,vault,s=setup;p=ready(store,1);date=int(time.time())+86400
    intent=s.prepare(p['id'],'schedule_text',date);s.claim(intent['id'],intent['digest'],True);client.post_error=VkError(27);s.dispatch(intent['id'])
    result=s.get(intent['id']);assert result['state']=='FAILED' and 'delivery' not in result['result']
    assert client.uploads==0 and sum(m=='wall.post' for m,_ in client.calls)==1


def test_schedule_timeout_uncertain_no_fallback_duplicate(setup):
    store,client,vault,s=setup;p=ready(store,1);date=int(time.time())+86400
    intent=s.prepare(p['id'],'schedule_text',date);s.claim(intent['id'],intent['digest'],True);client.post_error=VkError(uncertain=True);s.dispatch(intent['id'])
    assert s.get(intent['id'])['state']=='UNCERTAIN'
    assert not s.claim(intent['id'],intent['digest'],True)
    assert sum(m=='wall.post' for m,_ in client.calls)==1


def test_unavailable_read_is_not_verified_schedule(setup):
    store,client,vault,s=setup;p=ready(store);date=int(time.time())+86400;intent=s.prepare(p['id'],'schedule_text',date)
    s.claim(intent['id'],intent['digest'],True);client.read_error=VkError(27);s.dispatch(intent['id'])
    assert s.get(intent['id'])['result']['verification']=='UNAVAILABLE'


@pytest.mark.parametrize('date',[None,0,True,int(time.time())+100])
def test_schedule_requires_future_time(setup,date):
    store,client,vault,s=setup;p=ready(store)
    with pytest.raises(ValueError):s.prepare(p['id'],'schedule_text',date)


def test_runtime_photo27_fallback_only_before_external_effect(setup):
    store,client,vault,s=setup;s.connect(SECRET+'USER','user');s.probe_photos();p=ready(store,1);date=int(time.time())+86400
    intent=s.prepare(p['id'],'publish_with_fallback',date);assert intent['delivery_plan']=='wall'
    s.claim(intent['id'],intent['digest'],True);client.photo_error=VkError(27);s.dispatch(intent['id'])
    assert s.get(intent['id'])['result']['delivery']=='scheduled_text' and client.uploads==0


@pytest.mark.parametrize('preset',[[],{},None])
def test_invalid_preset_type(preset):
    with pytest.raises(ValueError):generate(SOURCE,channels=['vk'],preset=preset)


def test_promotion_preserves_late_conditions():
    source='Акция\n\nПервый абзац.\n\nВторой абзац.\n\nТретий абзац.\n\nЧетвёртый абзац.\n\nСкидка 10% до 30 ноября только при покупке двух станков.'
    r=generate(source,channels=['vk'],preset='promotion')
    assert 'Скидка 10% до 30 ноября только при покупке двух станков.' in r['drafts']['vk']['body']
