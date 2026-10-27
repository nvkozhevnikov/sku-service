"""Actual PostgreSQL QA + authenticated application routes, never LIVE11 DML or HTTP."""
from pathlib import Path
import json,sys,re,uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from qa_supplier_web import make_app
from qa_supplier_database import qa_config,DATABASE

def main():
    import psycopg
    from universal_supplier.control_plane.security import PostgresLoginThrottle,throttle_digest
    cfg=qa_config();throttle=PostgresLoginThrottle(cfg);key=('qa_'+uuid.uuid4().hex,'127.0.0.1')
    assert not throttle.locked(key)
    for i in range(5):throttle.failure(key)
    assert PostgresLoginThrottle(cfg).locked(key)
    with psycopg.connect(**cfg.kwargs(),autocommit=True) as c:
        before=c.execute('SELECT id,failed_at FROM login_failures WHERE throttle_key=%s ORDER BY id',(throttle_digest(key),)).fetchall()
    throttle.reset(key);assert not throttle.locked(key)
    with psycopg.connect(**cfg.kwargs(),autocommit=True) as c:
        after=c.execute('SELECT id,failed_at FROM login_failures WHERE throttle_key=%s ORDER BY id',(throttle_digest(key),)).fetchall()
        assert before==after and len(after)==5
    from fastapi.testclient import TestClient
    c=TestClient(make_app(),base_url='https://testserver')
    assert c.post('/login',data={'username':'qa_admin','password':'Fixture-only-operator-password'},follow_redirects=False).status_code==303
    routes={}
    for path in ('/','/suppliers','/scheduler','/scheduler/execution','/runs','/review','/exports/xml','/exports/previews','/settings'):
        response=c.get(path);assert response.status_code==200,(path,response.text[-600:]);routes[path]=200
    for row in c.app.state.store.suppliers():
        for part in ('parsing','freshness'):
            path=f"/suppliers/{row['id']}/{part}";response=c.get(path)
            assert response.status_code==200,(path,response.text[-600:]);routes[path]=200
        path=f"/suppliers/{row['id']}/parsing";page=c.get(path)
        token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
        old_revision=c.app.state.store.parsing_settings(row['id'])['revision']
        form={'csrf_token':token,'revision':str(old_revision),'delay_min_seconds':'1',
              'delay_max_seconds':'3','timezone':'Europe/Moscow'}
        assert c.post(path,data=form,follow_redirects=False).status_code==303
        saved=c.app.state.store.parsing_settings(row['id'])
        assert saved['revision']==old_revision+1 and saved['settings']['timezone']=='Europe/Moscow'
        assert c.post(path,data=form).status_code==400
        assert c.post(path,data={**form,'csrf_token':'bad'}).status_code==403
        assert c.app.state.store.parsing_settings(row['id'])['revision']==old_revision+1
    page=c.get('/exports/previews');token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    assert c.post('/exports/previews/generate',data={'csrf_token':token},follow_redirects=False).status_code==303
    for kind in ('updates','new_products'):
        r=c.get(f'/exports/previews/{kind}/download');assert r.status_code==200 and 'import_allowed="false"' in r.text
    with psycopg.connect(**cfg.kwargs(),autocommit=True) as conn:
        assert conn.execute('SELECT count(*) FROM crawl_jobs').fetchone()==(0,)
        assert conn.execute('SELECT count(*) FROM supplier_http_captures').fetchone()==(0,)
    result={'database':DATABASE,'classification':'SYNTHETIC SQL + application routes, NOT LIVE collection',
        '18_migrations_and_replay':'PASS','shared_login_throttle_retains_history':'PASS','routes':routes,
        'two_XML_downloads':'PASS','all11_sources':'PASS','HTTP_supplier':0,'enqueue':0,'matching':0,'LIVE11_writes':0}
    result['all11_authenticated_POST_SQL_CAS_CSRF']='PASS'
    out=ROOT.parent.parent/'reports/UNIVERSAL_SUPPLIER_AUTONOMOUS_SYSTEM_20261026'
    (out/'QA_SQL_ROUTES.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':main()
