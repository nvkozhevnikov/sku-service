"""Native mount security with real session/CSRF and disposable local data only."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from content_studio.storage import Store
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import UnavailableControlPlaneStore

PASSWORD='Synthetic-Only!Aa17'

class AclStore(Store):
    def __init__(self,path):super().__init__(path);self.grants={};self.audit_rows=[]
    def access(self,pid,user,write=False):
        grant=self.grants.get((pid,user.id))
        return user.role=='ADMIN' or grant is True or grant is False and not write
    def audit(self,*args):self.audit_rows.append(args)

class Vault:
    def available(self,ref):return False
    def get(self,ref):raise ValueError('TEST_NO_EXTERNAL_CREDENTIALS')

@pytest.fixture
def native(tmp_path,monkeypatch):
    monkeypatch.setenv('CONTROL_PLANE_INSECURE_HTTP','YES')
    encoded=hash_password(PASSWORD)
    rights=['content.read','content.edit','content.media','content.export','content.vk.publish','content.ai.use']
    rows=[dict(id=i,username=name,display_name=name,role=role,password_hash=encoded,
               permissions=permissions,must_change_password=forced)
          for i,name,role,permissions,forced in [(1,'admin','ADMIN',[],False),(2,'editor','OPERATOR',rights,False),
            (3,'other','OPERATOR',rights,False),(4,'reader','VIEWER',['content.read'],False),
            (5,'legacy','OPERATOR',None,False),(6,'forced','ADMIN',[],True)]]
    admin=InMemoryAdminStore(rows);store=AclStore(tmp_path/'studio');project=store.create('Private material')
    store.grants[(project['id'],2)]=True;store.grants[(project['id'],4)]=False
    app=create_app(store=UnavailableControlPlaneStore('offline QA'),admin_store=admin,auth_required=True,
       session_secret='synthetic-native-session-only-never-deployed',
       content_studio_options={'store':store,'ai_vault':Vault(),'vk_vault':Vault()})
    return app,admin,store,project

def login(native,name):
    c=TestClient(native[0]);assert c.post('/login',data={'username':name,'password':PASSWORD},follow_redirects=False).status_code==303
    r=c.get('/content-studio/api/session',follow_redirects=False)
    return c,{'X-CSRF-Token':r.json()['csrf']} if r.status_code==200 else {}

def test_single_session_and_native_assets(native):
    c,h=login(native,'editor');p=native[3]['id']
    assert 'studio_session' not in c.cookies
    assert c.get('/content-studio/').status_code==200
    assert c.get('/content-studio/static/base.js').status_code==200
    assert c.get('/content-studio/api/projects/'+p).status_code==200
    assert c.post('/content-studio/api/login',headers=h,json={}).status_code==403

def test_login_origin_guard_rejects_null_and_external_origin(native):
    c=TestClient(native[0])
    for origin in ('null','https://untrusted.invalid'):
        assert c.post('/login',headers={'Origin':origin},data={'username':'admin','password':PASSWORD}).status_code==403
    assert c.post('/login',headers={'Origin':'http://testserver'},data={'username':'admin','password':PASSWORD},follow_redirects=False).status_code==303
    assert c.get('/content-studio/').headers['referrer-policy']=='same-origin'

@pytest.mark.parametrize('suffix',['','/history','/export?kind=backup','/preview-window/news','/media/'+'a'*32])
def test_other_user_cannot_read_direct_project_ids(native,suffix):
    c,_=login(native,'other')
    assert c.get('/content-studio/api/projects/'+native[3]['id']+suffix).status_code==404

@pytest.mark.parametrize('method,suffix', [('PUT',''),('POST','/duplicate'),('POST','/restore/0'),('POST','/media'),('POST','/approve/news'),('POST','/preview/news')])
def test_other_user_cannot_mutate_direct_project_ids(native,method,suffix):
    c,h=login(native,'other')
    assert c.request(method,'/content-studio/api/projects/'+native[3]['id']+suffix,headers=h,json={}).status_code==404

def test_preview_is_bound_to_session_and_current_acl(native):
    c,h=login(native,'editor');p=native[3]['id']
    r=c.post('/content-studio/api/projects/'+p+'/preview/news',headers=h,json={'html':'<p>Private</p>'})
    assert r.status_code==200
    url=r.json()['preview_url'];assert c.get(url).status_code==200
    other,_=login(native,'other');assert other.get(url).status_code==404
    native[2].grants.pop((p,2));assert c.get(url).status_code==404

@pytest.mark.parametrize('name',['reader','legacy','forced'])
def test_read_only_legacy_and_forced_change_cannot_write(native,name):
    c,h=login(native,name)
    r=c.post('/content-studio/api/projects',headers=h,json={'title':'must not create'},follow_redirects=False)
    assert r.status_code==(303 if name=='forced' else 403)
    if name=='forced':assert r.headers['location']=='/account/password'
    if name=='legacy':assert c.get('/content-studio/').status_code==403

@pytest.mark.parametrize('change',['revoke','deactivate'])
def test_revocation_and_disabled_account_block_existing_studio_cookie(native,change):
    c,h=login(native,'editor')
    if change=='revoke':native[1].revoke_sessions(2,1,'QA')
    else:native[1].set_user_active(2,False,1)
    assert c.get('/content-studio/api/projects',follow_redirects=False).status_code==401
    assert c.post('/content-studio/api/projects',headers=h,json={}).status_code==401

def test_csrf_external_calls_and_admin_only_settings(native):
    c,h=login(native,'editor');p=native[3]['id']
    before=deepcopy(native[2].get(p))
    assert c.put('/content-studio/api/projects/'+p,headers={'X-CSRF-Token':'forged'},json={}).status_code==403
    assert c.post('/content-studio/api/projects/'+p+'/compose',headers=h,json={'engine':'ai'}).status_code==403
    assert c.post('/content-studio/api/sources/read',headers=h,json={'url':'https://sterbrust.com/catalog/x/'}).status_code==403
    assert c.post('/content-studio/api/ai/settings',headers=h,json={}).status_code==403
    assert c.post('/content-studio/api/vk/connect-desktop',headers=h,json={}).status_code==403
    assert native[2].get(p)==before

def test_pending_migration_never_falls_back_to_sqlite(tmp_path,monkeypatch):
    monkeypatch.delenv('CONTENT_STUDIO_ENABLED',raising=False)
    monkeypatch.setenv('CONTROL_PLANE_INSECURE_HTTP','YES')
    admin=InMemoryAdminStore([dict(id=1,username='admin',display_name='A',role='ADMIN',password_hash=hash_password(PASSWORD))])
    app=create_app(store=UnavailableControlPlaneStore('offline'),admin_store=admin,auth_required=True,
      session_secret='synthetic-pending-session-key-never-deployed')
    c=TestClient(app);c.post('/login',data={'username':'admin','password':PASSWORD})
    assert c.get('/content-studio/').status_code==503
    assert c.get('/content-studio/api/projects').json()['migration_required'] is True
    assert not list(tmp_path.rglob('*.sqlite3'))
