"""Explicit isolated Linux QA only. Never uses project/private runtime configs."""
import base64,hashlib,io,json,os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from PIL import Image
from fastapi.testclient import TestClient
from content_studio.migration import read_source,import_snapshot,target_fingerprints
from content_studio.postgres_store import PostgresStore,CURRENT_ACTOR
from content_studio.secret_store import ServerVault
from content_studio.vk import VkService
from universal_supplier.postgres import PostgresConfig
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app

pytestmark=pytest.mark.skipif(os.environ.get('STUDIO_ISOLATED_PG_QA')!='YES',reason='Explicit isolated PostgreSQL QA only')
DB=os.environ.get('STUDIO_QA_DATABASE','studio_integration_qa_linux_20261117')
assert DB.startswith('studio_integration_qa_linux_20261117') and DB.replace('_','').isalnum()
PASSWORD='Synthetic-Only-Studio!Aa17'
BUSINESS=('suppliers','catalog_products','source_products','offers','offer_commercial_observations','supplier_http_captures',
 'sterbrust_products','sterbrust_product_supplier_identifiers','product_matches','product_identity_decisions','review_cases','review_decisions')

def connect(role='rc_owner',**kwargs):
    assert os.environ.get('STUDIO_ISOLATED_PG_QA')=='YES'
    return psycopg.connect(host='127.0.0.1',port=5432,dbname=DB,user=role,**kwargs)

def business_hashes(c):
    # Match the frozen Windows JSON timestamp representation; values are unchanged.
    c.execute("SET TIME ZONE 'Africa/Nairobi'")
    return {t:list(c.execute(sql.SQL("SELECT count(*),md5(coalesce(string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),'')) FROM public.{} t").format(sql.Identifier(t))).fetchone()) for t in BUSINESS}

@pytest.fixture(scope='module')
def qa(tmp_path_factory):
    temp=tmp_path_factory.mktemp('studio-pg');source=Path('/qa/source');snapshot=read_source(source)
    baseline=json.loads(Path('/qa/PRIMARY_BEFORE.json').read_text())['public_business']
    with connect(autocommit=True) as c:
        assert c.execute('SELECT current_database(),current_setting(\'server_version_num\')').fetchone()==(DB,'170011')
        assert business_hashes(c)==baseline
        assert c.execute("SELECT to_regnamespace('content_studio')").fetchone()[0] is None
        # Deliberate file collision: schema/data and only newly written media roll back.
        broken=temp/'failed';(broken/'media').mkdir(parents=True)
        bad=broken/'media'/list(snapshot['media'])[-1];bad.write_bytes(b'deliberate isolated conflict')
        with pytest.raises(ValueError,match='TARGET_MEDIA_EXISTS_WITH_OTHER_BYTES'):
            import_snapshot(c,snapshot,broken,{},legacy_admin_only=True,apply_schema=True)
        assert c.execute("SELECT to_regnamespace('content_studio')").fetchone()[0] is None
        assert list((broken/'media').iterdir())==[bad]
        data=temp/'data';result=import_snapshot(c,snapshot,data,{},legacy_admin_only=True,apply_schema=True)
        assert result['status']=='IMPORTED_VERIFIED'
        assert target_fingerprints(c)==snapshot['manifest']['tables']
        again=import_snapshot(c,snapshot,data,{},legacy_admin_only=True,apply_schema=True)
        assert again['status']=='ALREADY_IMPORTED_NOT_REPLAYED'
        assert business_hashes(c)==baseline
        c.execute('GRANT USAGE ON SCHEMA public TO rc_runtime')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO rc_runtime')
        c.execute('GRANT INSERT,UPDATE ON public.app_users,public.app_access_profiles,public.audit_events,public.login_failures TO rc_runtime')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO rc_runtime')
    runtime=lambda:connect('rc_runtime',autocommit=True,row_factory=dict_row)
    store=PostgresStore(data,runtime)
    cfg=PostgresConfig('127.0.0.1',5432,DB,'rc_runtime',None,'disable')
    def adapter(config=cfg,**kwargs):
        assert config.dbname==DB and config.user=='rc_runtime'
        return connect('rc_runtime',**kwargs)
    admin=PostgresAdminStore(cfg,connection_factory=adapter)
    ids={}
    for name,role in [('studio_qa_admin','ADMIN'),('studio_qa_editor','OPERATOR'),('studio_qa_other','OPERATOR')]:
        ids[name]=admin.create_user(name,name,hash_password(PASSWORD),role)
    with connect(autocommit=True) as c:
        c.execute("INSERT INTO public.app_access_profiles(code,name,base_role,permissions) VALUES('QA_CONTENT','Isolated QA','OPERATOR',%s::jsonb)",
           (json.dumps(['content.read','content.edit','content.media','content.export','content.vk.publish']),))
        c.execute("UPDATE public.app_users SET access_profile='QA_CONTENT' WHERE id=ANY(%s)",
           ([ids['studio_qa_editor'],ids['studio_qa_other']],))
    vault=ServerVault(temp/'vault')
    app=create_app(store=UnavailableControlPlaneStore('isolated PG QA'),admin_store=admin,auth_required=True,
      session_secret='synthetic-isolated-native-PG-session-not-deployed',
      content_studio_options={'store':store,'ai_vault':vault,'vk_vault':vault})
    yield dict(app=app,store=store,admin=admin,ids=ids,snapshot=snapshot,baseline=baseline)
    with connect(autocommit=True) as c:assert business_hashes(c)==baseline
    assert read_source(source)['source_sha256']==snapshot['source_sha256']
    report={'status':'PASS','database':DB,'PostgreSQL':'17.11','runtime_role':'rc_runtime','owner_used_by_web':False,
      'tables':snapshot['manifest']['tables'],'media_files':len(snapshot['media']),'source_unchanged':True,
      'public_business_tables_exact':12,'primary_writes':0,'external_calls':0,'rollback_test':True,'repeat_no_replay':True}
    Path('/qa/output/LINUX_PG_VERIFIED.json').write_text(json.dumps(report,indent=2))

def login(qa,name):
    c=TestClient(qa['app'],base_url='https://testserver')
    assert c.post('/login',data={'username':name,'password':PASSWORD},follow_redirects=False).status_code==303
    r=c.get('/content-studio/api/session');assert r.status_code==200
    return c,{'X-CSRF-Token':r.json()['csrf']}

def test_runtime_is_not_owner_or_superuser_and_cannot_change_source_data(qa):
    with connect('rc_runtime',autocommit=True) as c:
        assert c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False)
        for query in ('DELETE FROM public.source_products WHERE false','CREATE TABLE content_studio.forbidden(id int)',
                      'SELECT * FROM content_studio.migration_receipts'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(query)
    assert not (qa['store'].root/'studio.sqlite3').exists()

def test_imported_projects_admin_only_and_native_editor_cas_media_export(qa):
    admin,_=login(qa,'studio_qa_admin');editor,h=login(qa,'studio_qa_editor');other,oh=login(qa,'studio_qa_other')
    original=qa['snapshot']['rows']['projects'][0]['id']
    assert len(admin.get('/content-studio/api/projects').json())==6
    assert editor.get('/content-studio/api/projects').json()==[]
    assert editor.get('/content-studio/api/projects/'+original+'/export?kind=backup').status_code==404
    p=editor.post('/content-studio/api/projects',headers=h,json={'title':'Isolated PG only'}).json();pid=p['id'];url='/content-studio/api/projects/'+pid
    assert other.get(url).status_code==404
    p['drafts']['news']['body']='<p style="color:#27222c">PG isolated content</p>'
    def save(_):
        with TestClient(qa['app'],base_url='https://testserver') as c:
            c.cookies.update(editor.cookies)
            return c.put(url,headers=h,json={'expected_revision':0,'project':p}).status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(save,range(2)))==[200,409]
    p=editor.get(url).json();image=io.BytesIO();Image.new('RGB',(80,60),'green').save(image,'JPEG')
    r=editor.post(url+'/media',headers=h,json={'expected_revision':p['revision'],'name':'QA','data':base64.b64encode(image.getvalue()).decode()})
    assert r.status_code==200;r=r.json();asset=r['media'][-1]['id']
    assert editor.get(url+'/media/'+asset).status_code==200
    assert other.get(url+'/media/'+asset).status_code==404
    preview=editor.post(url+'/preview/news',headers=h,json={'html':r['drafts']['news']['body']}).json()['preview_url']
    assert editor.get(preview).status_code==200 and other.get(preview).status_code==404
    assert editor.get(url+'/export?kind=backup').status_code==200
    assert len(editor.get(url+'/history').json())>=3
    assert editor.post('/content-studio/api/vk/connect',headers=h,json={}).status_code==403
    qa['admin'].revoke_sessions(qa['ids']['studio_qa_editor'],qa['ids']['studio_qa_admin'],'QA')
    assert editor.get(url,follow_redirects=False).status_code==401

def test_dispatch_lease_and_crash_recovery_are_cross_worker_and_no_retry(qa):
    store=qa['store'];pid=qa['snapshot']['rows']['projects'][0]['id'];ident='f'*32
    with connect(autocommit=True) as c:
        c.execute("INSERT INTO content_studio.vk_intents VALUES(%s,%s,0,%s,'{}','SENDING',0,'{}','QA')",(ident,pid,'synthetic-crash-lease'))
    with store.dispatch_guard(ident) as first:
        assert first
        with store.dispatch_guard(ident) as second:assert not second
        store.recover_inflight()
        with store.connection() as c:assert c.execute('SELECT state FROM vk_intents WHERE id=?',(ident,)).fetchone()[0]=='SENDING'
    store.recover_inflight();store.recover_inflight()
    with store.connection() as c:assert c.execute('SELECT state FROM vk_intents WHERE id=?',(ident,)).fetchone()[0]=='UNCERTAIN'

def test_vault_missing_legacy_ref_and_real_encryption_file_modes(qa,tmp_path):
    from cryptography.fernet import Fernet
    key=tmp_path/'master';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    vault=ServerVault(tmp_path/'private',key);secret='synthetic-credential-never-live-1234'
    ref=vault.put(secret);assert vault.get(ref)==secret
    assert secret.encode() not in (vault.root/(ref+'.fernet')).read_bytes()
    assert (vault.root/(ref+'.fernet')).stat().st_mode & 0o077==0
    assert not vault.available('old-dpapi-reference')
    key.chmod(0o644)
    with pytest.raises(ValueError):vault.get(ref)
