"""Existing Grok cabinet/LIVE11/news stay in the same app as new settings/XML."""
import csv
from pathlib import Path
from fastapi.testclient import TestClient
from test_automation_admin import Store
from universal_supplier.control_plane.cabinet import CabinetService
from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService
from universal_supplier.control_plane.web import create_app

def test_reused_cabinet_and_new_routes_coexist_without_replacing_supplier_landing(monkeypatch):
    store=Store();commercial=DisabledCommercialQaService('OFFLINE')
    # Inject this checkout: the default is an optional operator sibling worktree.
    cabinet=CabinetService(store,commercial,feature=Path(__file__).resolve().parents[1])
    matrix=list(csv.DictReader((cabinet.feature/'config/live11_approval_matrix.csv').open(encoding='utf-8-sig')))
    monkeypatch.setattr(cabinet,'batch',lambda bid=None:{'connected':False,'reason':'OFFLINE_QA',
        'explanation':'OFFLINE_QA','matrix':matrix,'payload':None,'launch_allowed':False})
    monkeypatch.setattr(cabinet,'source',lambda code,mode='REFRESH',limit=None,job_id=None:{
        'code':code,'mode':mode,'limit':limit,'job_id':job_id,'connected':False,'ready':False,
        'source':None,'jobs':[],'preview':None,'reason':'OFFLINE_QA','explanation':'OFFLINE_QA',
        'launch_allowed':False,'write_session_enabled':False})
    c=TestClient(create_app(store=store,auth_required=False,commercial_qa_service=commercial,cabinet_service=cabinet))
    page=c.get('/suppliers');assert page.status_code==200
    assert page.text.count('Открыть источник')==11
    for row in store.suppliers():
        assert c.get(f"/suppliers/{row['id']}/parsing").status_code==200
        assert c.get('/cabinet/sources/'+row['code']).status_code==200
    batch=c.get('/live11');assert batch.status_code==200 and '22 фазы' in batch.text
    assert c.get('/scheduler/execution').status_code==200
    assert c.get('/exports/previews').status_code==200
    assert c.get('/news',follow_redirects=False).headers['location']=='/competitor-monitoring/news'
    assert not c.app.state.queue.jobs and c.app.state.direct_crawl_executions==0
