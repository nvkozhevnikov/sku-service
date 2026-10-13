import json,sys,threading
from pathlib import Path
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from competitor_intel.execution import settings,run_competitor_collection,execution_lock,AlreadyRunning,EXIT_CODES

class Repo:
    def __init__(self):self.runs=[]
    def start(self,since,result):self.runs.append(deepcopy(result));return len(self.runs)
    def finish(self,id,result):self.runs[id-1]=deepcopy(result)
    def enabled_sources(self):return {'metalmaster','jet_online','youmet'}

def config():
    c=settings();c.update(enabled=True,selected_sources=['metalmaster','jet_online']);return c

def test_disabled_schedule_does_not_start_or_collect(tmp_path):
    c=config();c['enabled']=False;r=Repo()
    result=run_competitor_collection(r,lambda *a:pytest.fail('HTTP forbidden'),lambda *a:pytest.fail('Report forbidden'),origin='SCHEDULED',config=c,runtime=tmp_path)
    assert result['exit_code']==4 and not r.runs and not tmp_path.joinpath('last-run.json').exists()

@pytest.mark.parametrize('origin',['OPERATOR_UI','CLI_MANUAL','SCHEDULED'])
def test_same_runner_persists_origin_and_rebuilds(origin,tmp_path):
    r=Repo();calls=[]
    def collect(repo,code,run,*args):calls.append((repo,code,run));return {'status':'COMPLETED','stored':1}
    def report(repo,path,cfg):assert repo is r;return {'events':1,'html_path':str(path/'index.html')}
    out=run_competitor_collection(r,collect,report,origin=origin,config=config(),runtime=tmp_path)
    assert out['status']=='COMPLETED' and out['exit_code']==0 and out['report']['events']==1
    assert r.runs[0]['origin']==origin and len(calls)==2
    assert json.loads((tmp_path/'last-run.json').read_text())['origin']==origin

def test_source_failure_isolation(tmp_path):
    r=Repo()
    def collect(repo,code,*args):
        if code=='metalmaster':raise RuntimeError('secret-do-not-log')
        return {'status':'COMPLETED','stored':2}
    out=run_competitor_collection(r,collect,lambda *a:{'events':2},origin='SCHEDULED',config=config(),runtime=tmp_path)
    assert out['status']=='PARTIAL' and out['exit_code']==1 and out['sources']['jet_online']['stored']==2
    assert 'secret-do-not-log' not in (tmp_path/'collection.log').read_text()

def test_report_failure_retains_source_results(tmp_path):
    def fail(*a):raise RuntimeError()
    out=run_competitor_collection(Repo(),lambda *a:{'status':'COMPLETED','stored':3},fail,origin='SCHEDULED',config=config(),runtime=tmp_path)
    assert out['exit_code']==2 and out['sources']['metalmaster']['stored']==3

def test_manual_and_scheduled_share_lock(tmp_path):
    r=Repo()
    with execution_lock(tmp_path):
        for origin in ('OPERATOR_UI','SCHEDULED'):
            result=run_competitor_collection(r,lambda *a:pytest.fail('duplicate'),lambda *a:pytest.fail('duplicate'),origin=origin,config=config(),runtime=tmp_path)
            assert result['status']=='SKIP_ALREADY_RUNNING' and result['exit_code']==3
    assert not r.runs
    with execution_lock(tmp_path):pass

def test_lock_is_cross_process_and_released_on_exit(tmp_path):
    import subprocess
    code='from competitor_intel.execution import execution_lock; from pathlib import Path;\nwith execution_lock(Path('+repr(str(tmp_path))+')): print("acquired")'
    with execution_lock(tmp_path):
        proc=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        assert proc.returncode!=0 and 'AlreadyRunning' in proc.stderr
    proc=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert proc.returncode==0 and 'acquired' in proc.stdout

def test_stale_running_journal_is_marked_interrupted(tmp_path):
    r=Repo();r.runs=[{'status':'RUNNING','origin':'CLI_MANUAL'}]
    (tmp_path/'last-run.json').write_text(json.dumps({'status':'RUNNING','run_id':1,'origin':'CLI_MANUAL'}))
    out=run_competitor_collection(r,lambda *a:{'status':'COMPLETED'},lambda *a:{},origin='SCHEDULED',config=config(),runtime=tmp_path)
    assert r.runs[0]['reason']=='PROCESS_INTERRUPTED' and out['run_id']==2

def test_max_runtime_defers_without_second_collect(tmp_path):
    times=iter([0,0,1801]);calls=[]
    out=run_competitor_collection(Repo(),lambda *a:calls.append(a) or {'status':'COMPLETED'},lambda *a:{},origin='SCHEDULED',config=config(),runtime=tmp_path,clock=lambda:next(times))
    assert len(calls)==1 and out['status']=='PARTIAL' and out['sources']['jet_online']['reason']=='MAX_RUNTIME'

@pytest.mark.parametrize('patch',[{'enabled':'true'},{'selected_sources':['unknown']},{'max_runtime':0},{'skip_if_running':False},{'failure_policy':'RETRY_FOREVER'},{'timezone':'bad/zone'}])
def test_configuration_fails_closed(patch,tmp_path):
    cfg=config();cfg.update(patch);path=tmp_path/'cfg.json';path.write_text(json.dumps(cfg))
    with pytest.raises((ValueError,KeyError)):settings(path)

def test_scheduled_cli_disabled_noninteractive_exit4():
    import subprocess
    run=subprocess.run([sys.executable,'-m','competitor_intel','run-scheduled'],capture_output=True,text=True)
    assert run.returncode==4 and json.loads(run.stdout)['reason']=='SCHEDULER_DISABLED'

def test_scheduled_cli_uses_service_origin(monkeypatch,capsys):
    from competitor_intel import __main__ as cli
    import competitor_intel.execution as execution
    calls=[]
    class Service:
        def __init__(self,*args):pass
        def run(self,*args):calls.append(args);return {'status':'PARTIAL','exit_code':1}
    monkeypatch.setattr(execution,'CollectionService',Service);monkeypatch.setattr(sys,'argv',['competitor_intel','run-scheduled'])
    assert cli.main()==1 and calls==[('SCHEDULED',None,None)]

def test_scheduled_and_manual_rebuild_actual_detector_and_html(tmp_path):
    from tests.test_competitor_monitoring_ui import fixture_state
    from competitor_intel.business_data import normalized_dataset
    from competitor_intel.business_report import export_preview
    from datetime import date
    state=fixture_state();state['competitor_ai_analysis']=[]
    dataset=normalized_dataset(state);results=[]
    for origin in ('OPERATOR_UI','SCHEDULED'):
        out=run_competitor_collection(Repo(),lambda *a:{'status':'COMPLETED'},lambda repo,path,cfg:export_preview(dataset,date(2026,9,1),date(2026,10,12),path),origin=origin,config=config(),runtime=tmp_path/origin)
        events=json.loads((tmp_path/origin/'business-report/EVENTS.json').read_text(encoding='utf-8'))
        assert any(e['event_type']=='PRICE_DECREASE' and float(e['percent_delta'])==-14 for e in events)
        assert (tmp_path/origin/'business-report/index.html').exists()
        results.append(out['report']['business_facts_sha256'])
    assert results[0]==results[1]

def test_log_rotation_is_bounded(tmp_path):
    c=config();c.update(log_max_bytes=4096,log_backups=1)
    for _ in range(30):run_competitor_collection(Repo(),lambda *a:{'status':'COMPLETED'},lambda *a:{},origin='SCHEDULED',config=c,runtime=tmp_path)
    files=list(tmp_path.glob('collection.log*'))
    assert 1<=len(files)<=2 and all(p.stat().st_size<=4096 for p in files)

def test_scheduled_cli_fixture_integration_real_service(monkeypatch,tmp_path,capsys):
    from competitor_intel import __main__ as cli,collection_adapter,execution
    from competitor_intel.business_data import normalized_dataset
    from competitor_intel.business_report import export_preview
    from tests.test_competitor_monitoring_ui import fixture_state
    from datetime import date
    cfg=tmp_path/'fixture-schedule.json';cfg.write_text(json.dumps(config()))
    repo=Repo();repo.close=lambda:None;state=fixture_state();state['competitor_ai_analysis']=[];calls=[]
    monkeypatch.setattr(collection_adapter,'Repository',lambda config:repo)
    monkeypatch.setattr(collection_adapter,'collect_source',lambda repo,code,*args:calls.append(code) or {'status':'COMPLETED'})
    monkeypatch.setattr(collection_adapter,'rebuild_report',lambda repo,path,cfg:export_preview(normalized_dataset(state),date(2026,9,1),date(2026,10,12),path))
    original=execution.CollectionService
    monkeypatch.setattr(execution,'CollectionService',lambda db,schedule:original(db,schedule,tmp_path/'runtime'))
    monkeypatch.setattr(sys,'argv',['competitor_intel','run-scheduled','--schedule-config',str(cfg)])
    assert cli.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['origin']=='SCHEDULED' and result['report']['duplicate_events']==0 and calls==['metalmaster','jet_online']
    assert repo.runs[0]['origin']=='SCHEDULED' and (tmp_path/'runtime/business-report/index.html').exists()

def test_ui_post_uses_shared_service_and_refuses_cross_origin():
    from tests.test_competitor_monitoring_ui import service,ReadOnlyStore
    from universal_supplier.control_plane.web import create_app
    class Controller:
        def __init__(self):self.calls=[]
        def view(self):return {'config':settings(),'last':None,'manual':None,'scheduled':None,'running':False}
        def run(self,*args):self.calls.append(args);return {'status':'COMPLETED','exit_code':0}
    controller=Controller();app=create_app(store=ReadOnlyStore('fixture'),auth_required=False,competitor_service=service(),competitor_collection=controller)
    with TestClient(app) as c:
        response=c.post('/competitor-monitoring/runs',data={'source':'metalmaster','max_pages':'1'},headers={'origin':'http://testserver'},follow_redirects=False)
        assert response.status_code==303 and controller.calls==[('OPERATOR_UI',['metalmaster'],1)]
        assert c.post('/competitor-monitoring/runs',headers={'origin':'https://untrusted.example'}).status_code==403
        assert len(controller.calls)==1

def test_ui_duplicate_returns409():
    from tests.test_competitor_monitoring_ui import service,ReadOnlyStore
    from universal_supplier.control_plane.web import create_app
    class Controller:
        def run(self,*args):return {'status':'SKIP_ALREADY_RUNNING','exit_code':3}
    with TestClient(create_app(store=ReadOnlyStore('fixture'),auth_required=False,competitor_service=service(),competitor_collection=Controller())) as c:
        response=c.post('/competitor-monitoring/runs',headers={'origin':'http://testserver'})
        assert response.status_code==409 and response.json()['message']=='Сбор уже выполняется'
