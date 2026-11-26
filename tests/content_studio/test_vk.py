import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from content_studio.app import create_app
from content_studio.storage import Store,uid
from content_studio.vk import VkService,TARGET
from content_studio.vk_transport import VkClient,VkError,WindowsVault
from test_studio import media

SECRET='vk1.a.'+'SENSITIVE_TEST_VALUE_'*4


class MemoryVault:
    def __init__(self):self.data={}
    def put(self,token):ref=uid();self.data[ref]=token;return ref
    def get(self,ref):return self.data[ref]


class FakeVk:
    def __init__(self):self.calls=[];self.photo_error=None;self.post_error=None;self.read_error=None;self.wrong_group=False;self.uploads=0;self.text='';self.attachments=[]
    def call(self,method,token,params=None):
        self.calls.append((method,params or {}))
        if method=='groups.getTokenPermissions':return {'mask':8196,'permissions':[{'name':'photos'},{'name':'wall'}]}
        if method=='account.getAppPermissions':return 8196
        if method=='groups.getById':return {'groups':[{'id':1 if self.wrong_group else TARGET,'screen_name':'sterbrust','can_post':1}]}
        if method=='photos.getWallUploadServer':
            if self.photo_error:raise self.photo_error
            return {'upload_url':'https://pu.vk.com/upload?opaque=value'}
        if method=='photos.saveWallPhoto':return [{'owner_id':-TARGET,'id':self.uploads}]
        if method=='wall.post':
            if self.post_error:raise self.post_error
            self.text=params['message'];self.attachments=params['attachments'].split(',') if params['attachments'] else [];self.date=params.get('publish_date')
            return {'post_id':123}
        if method=='wall.getById':
            if self.read_error:raise self.read_error
            return {'items':[{'id':123,'owner_id':-TARGET,'text':self.text,'date':getattr(self,'date',None),'attachments':[{'type':'photo','photo':{'owner_id':-TARGET,'id':int(x.rsplit('_',1)[1])}} for x in self.attachments]}]}
        raise AssertionError('Unexpected method')
    def upload(self,url,data):self.uploads+=1;return {'server':1,'photo':'opaque','hash':'opaque'}


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path);client=FakeVk();vault=MemoryVault();service=VkService(store,client,vault)
    service.connect(SECRET)
    return store,client,vault,service


def ready(store,photos=0):
    p=store.create('Проверка VK')
    for _ in range(photos):p=media(store,p)
    for a in p['media']:a['rights']='QA only'
    p['facts']=[{'text':'Факт','evidence':'Источник','state':'CONFIRMED','valid_until':''}];p['drafts']['vk']['body']='Проверяемый текст'
    p=store.save(p['id'],p,p['revision']);return store.approve(p['id'],'vk',p['revision'])


def test_connect_read_only_no_secret_return_or_db(setup):
    store,client,vault,s=setup
    assert s.status()['community_connected']
    assert [x[0] for x in client.calls]==['groups.getTokenPermissions','groups.getById']
    assert SECRET not in json.dumps(s.status())
    assert SECRET.encode() not in (store.root/'studio.sqlite3').read_bytes()


def test_wrong_community_not_saved(setup):
    store,client,vault,s=setup;client.wrong_group=True
    previous=s._settings()
    with pytest.raises(ValueError):s.connect(SECRET)
    assert s._settings()==previous


def test_photo_27_block_does_not_drop_images(setup):
    store,client,vault,s=setup;client.photo_error=VkError(27)
    assert s.probe_photos()['photo_error']==27
    p=ready(store,2)
    with pytest.raises(ValueError):s.prepare(p['id'])
    assert len(store.get(p['id'])['drafts']['vk']['media'])==2
    assert not any(m in ('wall.post','photos.saveWallPhoto') for m,_ in client.calls)
    assert client.uploads==0


def test_prepare_is_inert_and_deduplicated(setup):
    store,client,vault,s=setup;p=ready(store)
    first=s.prepare(p['id']);second=s.prepare(p['id'])
    assert first['id']==second['id']
    assert first['message']=='Проверяемый текст'
    assert not any(m=='wall.post' for m,_ in client.calls)
    with pytest.raises(ValueError):s.claim(first['id'],first['digest'],False)


def test_double_confirm_one_claim_and_one_post(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id'])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:s.claim(intent['id'],intent['digest'],True),range(2)))
    assert sorted(results)==[False,True]
    s.dispatch(intent['id']);s.dispatch(intent['id'])
    result=s.get(intent['id']);assert result['state']=='APPLIED';assert result['result']['verification']=='VERIFIED'
    assert sum(m=='wall.post' for m,_ in client.calls)==1
    assert s.status()['text_capability']=='verified'


def test_duplicate_workers_cannot_dispatch_twice(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:s.dispatch(intent['id']),range(2)))
    assert sum(m=='wall.post' for m,_ in client.calls)==1


def test_manual_reconciliation_not_api_verification(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    client.post_error=VkError(uncertain=True);s.dispatch(intent['id'])
    result=s.record_found(intent['id'],456)
    assert result['state']=='MANUAL_RECORDED'
    assert result['result']['verification']=='MANUAL_RECORDED'
    assert sum(m=='wall.post' for m,_ in client.calls)==1


def test_edit_after_prepare_blocks_confirm(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id'])
    p['drafts']['vk']['body']='Новая версия';store.save(p['id'],p,p['revision'])
    with pytest.raises(ValueError):s.claim(intent['id'],intent['digest'],True)
    assert s.get(intent['id'])['state']=='PREPARED'


def test_token_change_invalidates_confirm(setup):
    from content_studio.storage import Conflict
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.connect(SECRET+'X')
    with pytest.raises(Conflict):s.claim(intent['id'],intent['digest'],True)


def test_timeout_uncertain_restart_never_reposts(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    client.post_error=VkError(uncertain=True);s.dispatch(intent['id'])
    assert s.get(intent['id'])['state']=='UNCERTAIN'
    reopened=VkService(store,client,vault)
    assert not reopened.claim(intent['id'],intent['digest'],True)
    with pytest.raises(ValueError):reopened.prepare(p['id'])
    with pytest.raises(ValueError):reopened.connect(SECRET)
    assert sum(m=='wall.post' for m,_ in client.calls)==1


def test_crash_after_claim_is_uncertain_on_boot(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    reopened=VkService(store,client,vault)
    assert reopened.get(intent['id'])['state']=='UNCERTAIN'
    assert not any(m=='wall.post' for m,_ in client.calls)


def test_definite_api_rejection_is_failed_not_applied(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    client.post_error=VkError(27);s.dispatch(intent['id'])
    assert s.get(intent['id'])['state']=='FAILED'
    assert s.get(intent['id'])['result']['error_code']==27
    assert not s.claim(intent['id'],intent['digest'],True)


def test_full_photo_order_and_read_unavailable(setup):
    store,client,vault,s=setup;s.connect(SECRET+'USER','user');s.probe_photos();p=ready(store,2)
    intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True);client.read_error=VkError(27);s.dispatch(intent['id'])
    result=s.get(intent['id']);assert result['state']=='APPLIED';assert result['result']['verification']=='UNAVAILABLE'
    assert client.attachments==[f'photo-{TARGET}_1',f'photo-{TARGET}_2']
    assert s.status()['photo_capability']=='verified'


def test_failure_at_photo_save_never_silently_posts_text(setup):
    store,client,vault,s=setup;s.connect(SECRET+'USER','user');s.probe_photos();p=ready(store,1);intent=s.prepare(p['id']);s.claim(intent['id'],intent['digest'],True)
    original=client.call
    def fail(method,token,params=None):
        if method=='photos.saveWallPhoto':raise RuntimeError(SECRET)
        return original(method,token,params)
    client.call=fail;s.dispatch(intent['id']);result=s.get(intent['id'])
    assert result['state']=='UNCERTAIN';assert SECRET not in json.dumps(result)
    assert not any(m=='wall.post' for m,_ in client.calls)


def test_expired_intent_can_be_prepared_again_without_dispatch(setup):
    store,client,vault,s=setup;p=ready(store);intent=s.prepare(p['id'])
    with store.connection() as c:c.execute('UPDATE vk_intents SET expires=0 WHERE id=?',(intent['id'],))
    with pytest.raises(ValueError):s.claim(intent['id'],intent['digest'],True)
    refreshed=s.prepare(p['id']);assert refreshed['expires_at']>0
    assert refreshed['id']==intent['id']


@pytest.mark.parametrize('url',['https://127.0.0.1/upload','https://api.vk.com/upload','http://pu.vk.com/upload','https://evil.example/upload','https://pu.vk.com.evil.example/upload','https://user:pass@pu.vk.com/upload'])
def test_upload_rejects_wrong_hosts_before_network(url):
    with pytest.raises(ValueError):VkClient().upload(url,b'image')


def test_local_routes_require_csrf_and_prepare_before_network(tmp_path):
    app=create_app(tmp_path,'code');c=TestClient(app)
    assert c.get('/api/vk/status').status_code==401
    csrf=c.post('/api/login',json={'code':'code'}).json()['csrf']
    assert c.post('/api/vk/connect-desktop',json={}).status_code==403
    assert c.get('/api/vk/status').json()['community_connected'] is False
    assert c.post('/api/vk/prepare/missing',json={},headers={'X-CSRF-Token':csrf}).status_code==404


def test_windows_vault_ciphertext_and_reopen(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Windows DPAPI contract')
    v=WindowsVault(tmp_path);ref=v.put(SECRET)
    assert SECRET.encode() not in (tmp_path/(ref+'.dpapi')).read_bytes()
    assert WindowsVault(tmp_path).get(ref)==SECRET
