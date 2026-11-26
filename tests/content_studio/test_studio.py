import base64
import io
import json
import zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from content_studio.app import create_app
from content_studio.content import digest, image_bytes, public_url, render, sanitize
from content_studio.exporting import import_project, make_export
from content_studio.storage import Conflict, Store


def picture(color='red'):
    f=io.BytesIO();Image.new('RGB',(60,90),color).save(f,'PNG');return f.getvalue()


@pytest.fixture
def store(tmp_path):return Store(tmp_path/'data')


def media(store,p,color='red'):
    raw=picture(color);jpg,meta=image_bytes(raw)
    return store.media_add(p['id'],raw,jpg,meta,p['revision'])


def test_cas_and_reopen_preserve_history(store):
    p=store.create('Черновик');old=deepcopy(p);p['drafts']['vk']['body']='Новый текст'
    p=store.save(p['id'],p,0)
    with pytest.raises(Conflict):store.save(p['id'],old,0)
    reopened=Store(store.root).get(p['id'])
    assert reopened['drafts']['vk']['body']=='Новый текст'
    assert store.revision(p['id'],0)['drafts']['vk']['body']==''
    assert len(store.history(p['id']))==2


def test_channels_and_approval_invalidation(store):
    p=store.create();p['facts']=[{'text':'Факт','evidence':'Документ','state':'CONFIRMED','valid_until':''}]
    p['drafts']['vk']['body']='VK';p['drafts']['news']['body']='<p>Сайт</p>'
    p=store.save(p['id'],p,0);p=store.approve(p['id'],'vk',1)
    p['drafts']['vk']['body']='Правка'
    p=store.save(p['id'],p,p['revision'])
    assert p['drafts']['vk']['state']=='DRAFT'
    assert p['drafts']['news']['body']=='<p>Сайт</p>'


@pytest.mark.parametrize('value',['https://localhost/a','http://site.example/a','https://127.0.0.1/a','https://[::1]/a','https://169.254.169.254/a','https://user:pass@example.com/a','https://example.com/a?token=x','file:///a','javascript:alert(1)','https://10.0.0.1/a'])
def test_final_urls_reject_local_or_sensitive(value):assert not public_url(value)


@pytest.mark.parametrize('raw',['<script>alert(1)</script><p>Текст</p>', '<img src=x onerror=alert(1)>','<svg onload=alert(1)>x</svg>','<p style="background:url(https://bad.test/x)">Текст</p>','<a href="javascript:alert(1)">x</a>','<iframe src="https://example.com"></iframe>'])
def test_html_never_retains_active_content(raw):
    safe,changed=sanitize(raw)
    assert changed
    assert not any(x in safe for x in ('<script','onerror','<svg','url(','javascript:','<iframe'))


def test_escaped_text_does_not_become_markup():
    safe,_=sanitize('&lt;img src=x onerror=alert(1)&gt;')
    assert safe.startswith('&lt;')


def test_bad_media_does_not_commit(store):
    p=store.create()
    with pytest.raises(ValueError):image_bytes(b'<svg/>')
    assert store.get(p['id'])['revision']==0


def test_order_hashes_export_restore_and_final_map(store):
    p=store.create('4 fixtures — не B3499')
    for c in ('red','green','blue','white'):p=media(store,p,c)
    order=list(reversed(p['drafts']['vk']['media']));p['drafts']['vk']['media']=order
    p['drafts']['vk']['body']='Проверка порядка';p['drafts']['news']['body']='<h2>Обзор</h2><table><tr><td>Факт</td></tr></table>'
    p=store.save(p['id'],p,p['revision'])
    data=make_export(store,p,'backup');assert data==make_export(store,p,'backup')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['ordered_media']['vk']==order
        for name,v in manifest['files'].items():assert digest(z.read(name))==v['sha256']
        assert Image.open(io.BytesIO(z.read('vk/media/01.jpg'))).size==(1200,1200)
    restored=import_project(store,data)
    assert restored['id']!=p['id']
    byid={a['id']:a for a in restored['media']};old={a['id']:a for a in p['media']}
    assert [byid[x]['original_hash'] for x in restored['drafts']['vk']['media']]==[old[x]['original_hash'] for x in order]
    assert all(d['state']=='DRAFT' for d in restored['drafts'].values())
    with pytest.raises(ValueError):make_export(store,p,'final')
    p['asset_map']={x:'https://sterbrust.com/upload/'+x+'.jpg' for x in order}
    final=make_export(store,p,'final')
    with zipfile.ZipFile(io.BytesIO(final)) as z:assert b'localhost' not in z.read('site/news/body.html')


def test_corrupt_import_leaves_no_project(store):
    p=store.create();data=make_export(store,p,'backup')
    dest=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src,zipfile.ZipFile(dest,'w') as z:
        for name in src.namelist():z.writestr(name,b'changed' if name=='vk/post.txt' else src.read(name))
    before=store.list()
    with pytest.raises(ValueError):import_project(store,dest.getvalue())
    assert store.list()==before


def test_traversal_rejected(store):
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:z.writestr('../escape','x')
    with pytest.raises(ValueError):import_project(store,f.getvalue())


def test_http_auth_csrf_origin_no_write(tmp_path):
    app=create_app(tmp_path,'local-code');c=TestClient(app)
    assert c.get('/api/projects').status_code==401
    assert c.post('/api/login',json={'code':'wrong'}).status_code==401
    response=c.post('/api/login',json={'code':'local-code'});csrf=response.json()['csrf']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert c.post('/api/projects',json={}).status_code==403
    headers={'X-CSRF-Token':csrf}
    assert c.post('/api/projects',json={},headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert c.get('/api/projects',headers={'Host':'evil.example'}).status_code==403
    p=c.post('/api/projects',json={},headers=headers).json()
    p['drafts']['vk']['body']='Сохранено'
    r=c.put('/api/projects/'+p['id'],json={'project':p,'expected_revision':0},headers=headers)
    assert r.status_code==200
    assert c.put('/api/projects/'+p['id'],json={'project':p,'expected_revision':0},headers=headers).status_code==409
    assert c.post('/api/publish/vk',json={},headers=headers).status_code==403
    assert c.post('/api/projects/'+p['id']+'/suggest/vk',json={},headers=headers).status_code==400
    assert 'frame-ancestors' in c.get('/').headers['content-security-policy']


def test_revision_conflict_media_cannot_change_project(store):
    p=store.create();p=media(store,p);raw=picture();jpg,meta=image_bytes(raw)
    with pytest.raises(Conflict):store.media_add(p['id'],raw,jpg,meta,0)
    assert len(store.get(p['id'])['media'])==1


def test_unreviewed_facts_block_approval(store):
    p=store.create();p['drafts']['vk']['body']='Текст';p=store.save(p['id'],p,0)
    with pytest.raises(ValueError):store.approve(p['id'],'vk',1)


def test_untrusted_html_requires_explicit_cleaning(store):
    p=store.create();p['drafts']['news']['body']='<p onclick="bad()">Текст</p>'
    with pytest.raises(ValueError):store.save(p['id'],p,0)
    assert store.get(p['id'])['revision']==0


def test_source_change_invalidates_approval(store):
    p=store.create();p['drafts']['vk']['body']='Факт';p['facts']=[{'text':'Факт','evidence':'Документ','state':'CONFIRMED','valid_until':''}]
    p=store.save(p['id'],p,0);p=store.approve(p['id'],'vk',1)
    p['source']['text']='Новый исходник';p=store.save(p['id'],p,p['revision'])
    assert p['drafts']['vk']['state']=='DRAFT'


def test_unknown_storage_schema_not_downgraded(tmp_path):
    import sqlite3
    tmp_path.mkdir(exist_ok=True)
    c=sqlite3.connect(tmp_path/'studio.sqlite3');c.execute('PRAGMA user_version=99');c.close()
    with pytest.raises(ValueError):Store(tmp_path)
    c=sqlite3.connect(tmp_path/'studio.sqlite3');assert c.execute('PRAGMA user_version').fetchone()[0]==99;c.close()


def test_invalid_fact_date_not_silently_approved(store):
    p=store.create();p['facts']=[{'text':'Факт','evidence':'Источник','state':'CONFIRMED','valid_until':'неизвестно'}]
    with pytest.raises(ValueError):store.save(p['id'],p,0)


def test_library_summary_excludes_content_and_tracks_readiness(store):
    p=store.create('Обзор');p=media(store,p);p['media'][0]['rights']='Own';p['drafts']['vk']['body']='Не возвращать тело в списке'
    p['facts']=[{'text':'Факт','evidence':'Источник','state':'CONFIRMED','valid_until':''}]
    p=store.save(p['id'],p,p['revision']);p=store.approve(p['id'],'vk',p['revision'])
    row=store.list()[0]
    assert row['media_count']==1 and row['ready_channels']==['vk']
    assert 'payload' not in row and 'body' not in row


def test_duplicate_preserves_original_and_resets_ready(tmp_path):
    app=create_app(tmp_path,'code');client=TestClient(app)
    csrf=client.post('/api/login',json={'code':'code'}).json()['csrf'];headers={'X-CSRF-Token':csrf}
    store=app.state.store;p=store.create('Исходный');p=media(store,p)
    p['media'][0]['rights']='Own';p['drafts']['vk']['body']='Исходный текст';p['facts']=[{'text':'Факт','evidence':'Источник','state':'CONFIRMED','valid_until':''}]
    p=store.save(p['id'],p,p['revision']);p=store.approve(p['id'],'vk',p['revision'])
    assert client.post('/api/projects/'+p['id']+'/duplicate',json={}).status_code==403
    r=client.post('/api/projects/'+p['id']+'/duplicate',json={},headers=headers)
    assert r.status_code==200
    copy=r.json();assert copy['id']!=p['id'] and copy['title']=='Копия — Исходный'
    assert copy['drafts']['vk']['body']==p['drafts']['vk']['body'] and copy['drafts']['vk']['state']=='DRAFT'
    assert copy['media'][0]['id']!=p['media'][0]['id']
    assert copy['media'][0]['original_hash']==p['media'][0]['original_hash']
    assert store.get(p['id'])==p
    assert app.state.vk.history()==[]


def test_preview_matches_selected_photos_and_no_remote_urls(tmp_path):
    app=create_app(tmp_path,'code');client=TestClient(app);csrf=client.post('/api/login',json={'code':'code'}).json()['csrf']
    p=app.state.store.create();p=media(app.state.store,p,'red');p=media(app.state.store,p,'blue')
    ids=[a['id'] for a in p['media']]
    raw=f'<p>Текст</p><img data-asset-id="{ids[0]}" alt="Фото">'
    response=client.post('/api/projects/'+p['id']+'/preview/news',json={'html':raw,'media':ids},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    preview=response.json()['html'];assert preview.count('data:image/jpeg;base64,')==2
    assert '<script' not in preview and 'http:' not in preview and 'https:' not in preview
    assert client.post('/api/projects/'+p['id']+'/preview/news',json={'html':'','media':['unknown']},headers={'X-CSRF-Token':csrf}).status_code==400


def test_preview_document_is_private_sandboxed_and_nonce_styled(tmp_path):
    app=create_app(tmp_path,'code');client=TestClient(app);csrf=client.post('/api/login',json={'code':'code'}).json()['csrf'];p=app.state.store.create()
    preview=client.post('/api/projects/'+p['id']+'/preview/news',json={'html':'<p style="text-align:center">Текст</p>','title':'<script>test</script>'},headers={'X-CSRF-Token':csrf}).json()
    page=client.get(preview['preview_url'])
    assert page.status_code==200 and 'nonce=' in page.text
    assert "script-src 'none'" in page.headers['content-security-policy'] and 'sandbox' in page.headers['content-security-policy']
    assert 'unsafe-inline' not in page.headers['content-security-policy']
    assert '&lt;script&gt;' in page.text and '<script>' not in page.text
    other=TestClient(app);other.post('/api/login',json={'code':'code'})
    assert other.get(preview['preview_url']).status_code==404
    app.state.preview_documents[preview['preview_url'].rsplit('/',1)[1]]['expires']=0
    assert client.get(preview['preview_url']).status_code==404
