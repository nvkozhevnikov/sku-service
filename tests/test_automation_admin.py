"""Real authenticated routes/settings/exports, injected fixtures; no LIVE or SQL."""
from copy import deepcopy
import re
import pytest
from fastapi.testclient import TestClient
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.supplier_automation import settings_for,public_settings,validate_settings,rate_for
from test_automation_system import snapshot,NOW

PASSWORD='Fixture-only-operator-password'


class Store(UnavailableControlPlaneStore):
    read_only=False
    def __init__(self):
        super().__init__('OFFLINE UI QA, NOT LIVE')
        self.data=snapshot();self.saved=[]
    def suppliers(self):
        return [{**r,'products':0,'job_id':None,'last_run_metadata':None} for r in self.data['suppliers']]
    def dashboard(self):return self.suppliers()
    def parsing_settings(self,sid):
        row=next((s for s in self.data['suppliers'] if s['id']==sid),None)
        if row is None:raise KeyError(sid)
        return {**public_settings(row,NOW),'supplier':row,'phases':[],'history':[]}
    def save_parsing_settings(self,sid,values,expected_revision,actor_id=None):
        row=next(s for s in self.data['suppliers'] if s['id']==sid);cfg=settings_for(row)
        if cfg['revision']!=expected_revision:raise ValueError('SETTINGS_REVISION_CHANGED')
        changed=validate_settings(values,cfg);changed['revision']+=1
        row['crawl_settings']['automation']=changed;row['rate_settings']=rate_for(changed,row['rate_settings'])
        self.saved.append((sid,actor_id,deepcopy(changed)))
    def dual_preview_snapshot(self):return deepcopy(self.data)


def client(tmp_path,monkeypatch,role='ADMIN'):
    monkeypatch.setenv('CONTROL_PLANE_EXPORT_ROOT',str(tmp_path/'exports'))
    store=Store();admin=InMemoryAdminStore([{'id':1,'username':'operator','display_name':'QA Operator',
        'role':role,'is_active':True,'password_hash':hash_password(PASSWORD)}])
    c=TestClient(create_app(store=store,admin_store=admin,auth_required=True,
        session_secret='qa-automation-session-secret-long-enough'),base_url='https://testserver')
    assert c.post('/login',data={'username':'operator','password':PASSWORD},follow_redirects=False).status_code==303
    return c,store


def csrf(response):return re.search(r'name="csrf_token" value="([^"]+)"',response.text).group(1)


def test_all11_authenticated_settings_and_cas_do_not_enqueue(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    assert c.get('/suppliers').status_code==200
    for supplier in store.suppliers():
        response=c.get(f"/suppliers/{supplier['id']}/parsing")
        assert response.status_code==200 and 'Настройки парсинга' in response.text and 'REFRESH' in response.text
    form={'csrf_token':csrf(response),'revision':'0','delay_min_seconds':'1','delay_max_seconds':'3',
          'refresh_enabled':'true','refresh_interval':'21600','discovery_enabled':'true','discovery_interval':'86400',
          'start_time':'09:30','timezone':'Europe/Moscow'}
    assert c.post('/suppliers/1/parsing',data=form,follow_redirects=False).status_code==303
    assert store.saved[-1][1]==1 and store.saved[-1][2]['delay_max_seconds']==3
    assert settings_for(store.data['suppliers'][1])['revision']==0
    assert c.post('/suppliers/1/parsing',data=form).status_code==400
    assert len(store.saved)==1
    assert c.app.state.direct_crawl_executions==0 and not c.app.state.queue.jobs


@pytest.mark.parametrize('case',['csrf','origin','viewer','anonymous'])
def test_settings_security_and_protected_routes(tmp_path,monkeypatch,case):
    c,store=client(tmp_path,monkeypatch,'VIEWER' if case=='viewer' else 'ADMIN')
    token=csrf(c.get('/suppliers/1/parsing'))
    headers={'origin':'http://127.0.0.1'}
    if case=='origin':headers['origin']='https://attacker.invalid'
    if case=='anonymous':c.cookies.clear()
    r=c.post('/suppliers/1/parsing',data={'revision':'0','csrf_token':'bad' if case=='csrf' else token},headers=headers)
    assert r.status_code==(401 if case=='anonymous' else 403)
    assert not store.saved


def test_real_export_routes_empty_then_generate_then_verified_download(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    response=c.get('/exports/previews');assert response.status_code==200 and 'ещё не сформированы' in response.text
    assert c.get('/exports/previews/updates/download').status_code==404
    response=c.post('/exports/previews/generate',data={'csrf_token':csrf(response)},follow_redirects=False)
    assert response.status_code==303,response.text[-1200:]
    page=c.get('/exports/previews');assert 'Записей: 1' in page.text and 'BLOCKED_IMPORT_CONTRACT_EVIDENCE' in page.text
    for kind in ('updates','new_products'):
        response=c.get(f'/exports/previews/{kind}/download')
        assert response.status_code==200 and 'import_allowed="false"' in response.text
        assert '.preview.xml' in response.headers['content-disposition']
    assert c.get('/exports/previews/unknown/download').status_code==404
    assert c.get('/suppliers/1/freshness').status_code==200
    assert c.get('/settings').status_code==200 and 'Два XML-предпросмотра' not in c.get('/settings').text
    assert c.get('/scheduler').status_code==200
    assert c.app.state.direct_crawl_executions==0
