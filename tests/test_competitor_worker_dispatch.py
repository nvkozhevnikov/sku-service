import json,os,time,sys,subprocess,socket,signal
from pathlib import Path
import pytest
from competitor_intel.execution import CollectionService,settings,execution_lock
from competitor_intel.worker_dispatch import liveness,process_identity,load

def fixture_service(path,**spec):
    path.mkdir(parents=True,exist_ok=True)
    (path/'fixture.json').write_text(json.dumps({'mode':'ISOLATED_FIXTURE_ONLY',**spec}),encoding='utf-8')
    cfg=settings();cfg.update(selected_sources=['metalmaster'],max_runtime=30,max_pages_per_source=1)
    (path/'schedule.json').write_text(json.dumps(cfg),encoding='utf-8')
    return CollectionService(None,path/'schedule.json',path,fixture_spec=path/'fixture.json')

def wait_for(fn,seconds=12):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.05)
    raise AssertionError('Fixture condition timed out')

def test_pid_reuse_and_unknown_are_not_alive(monkeypatch):
    import competitor_intel.worker_dispatch as d
    old={'pid':1,'created':1,'executable':'worker'}
    monkeypatch.setattr(d,'process_identity',lambda pid:{**old,'created':2})
    assert liveness(old)=='PID_REUSED'
    monkeypatch.setattr(d,'process_identity',lambda pid:None);assert liveness(old)=='GONE'
    assert liveness({'pid':1,'unknown':True})=='UNKNOWN'

def test_scheduler_disabled_and_bad_selection_do_not_spawn(tmp_path):
    s=fixture_service(tmp_path)
    assert s.submit('SCHEDULED')['reason']=='SCHEDULER_DISABLED'
    with pytest.raises(ValueError):s.submit('OPERATOR_UI',['not_a_source'],1)
    assert not (tmp_path/'worker-dispatch.json').exists()

def test_existing_kernel_lock_blocks_submission(tmp_path):
    s=fixture_service(tmp_path)
    with execution_lock(tmp_path):assert s.submit('OPERATOR_UI')['status']=='SKIP_ALREADY_RUNNING'
    assert not (tmp_path/'worker-dispatch.json').exists()

def test_fixture_backend_cannot_use_business_runtime(tmp_path):
    from competitor_intel.execution import ROOT
    with pytest.raises(ValueError):CollectionService(None,None,ROOT/'competitor-data/collection',fixture_spec=tmp_path/'fixture.json')

def test_failed_worker_finalizes_without_secret_leak(tmp_path):
    s=fixture_service(tmp_path,fail=True);result=s.submit('OPERATOR_UI',['metalmaster'],1)
    assert result['status']!='FAILED' or result.get('run_id')
    done=wait_for(lambda:(r:=load(tmp_path/'last-run.json')) and r['status']=='FAILED' and r)
    assert done['origin']=='OPERATOR_UI' and done['finished_at'] and done['run_id']==1
    assert done['errors'][0]['reason']=='RuntimeError'
    wait_for(lambda:liveness(done['worker_identity'])!='ALIVE')
    assert fixture_service(tmp_path,fail=True).view()['last']['status']=='FAILED'

def test_bootstrap_orphan_is_not_automatically_restarted(tmp_path):
    s=fixture_service(tmp_path)
    (tmp_path/'fixture-runs.json').write_text(json.dumps([{'id':1,'status':'RUNNING'}]))
    assert s.submit('OPERATOR_UI')['status']=='START_BLOCKED_ORPHAN'
    assert not (tmp_path/'worker-dispatch.json').exists()

def test_spawn_failure_is_persistent_and_does_not_create_run(monkeypatch,tmp_path):
    s=fixture_service(tmp_path)
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:(_ for _ in ()).throw(OSError('private-text')))
    assert s.submit('OPERATOR_UI')['reason']=='WORKER_SPAWN_FAILED'
    assert s.view()['active']['status']=='FAILED'
    assert 'private-text' not in (tmp_path/'worker-dispatch.json').read_text()
    assert not (tmp_path/'fixture-runs.json').exists()

def test_pending_start_reservation_blocks_cli_and_second_UI(tmp_path):
    from competitor_intel.execution import atomic_json
    s=fixture_service(tmp_path)
    atomic_json(tmp_path/'worker-dispatch.json',{'request_id':'reserved','status':'STARTING','launcher_identity':process_identity(os.getpid())})
    assert s.submit('OPERATOR_UI')['status']=='SKIP_ALREADY_RUNNING'
    assert s.run('CLI_MANUAL')['status']=='SKIP_ALREADY_RUNNING'
    assert not (tmp_path/'fixture-runs.json').exists()

def test_actual_worker_disappearance_is_orphan_not_UI_failure(tmp_path):
    s=fixture_service(tmp_path);s.submit('OPERATOR_UI',['metalmaster'],1)
    wait_for(lambda:load(tmp_path/'fixture-entered.json'));active=s.view()['active'];identity=active['worker_identity']
    os.kill(identity['pid'],signal.SIGTERM);wait_for(lambda:liveness(identity)=='GONE')
    # Exit-code visibility may precede final kernel-handle teardown on Windows.
    view=wait_for(lambda:(v:=s.view()) and v['run_health']=='ORPHANED' and v)
    assert not view['running']
    assert view['active']['status']=='RUNNING'
    assert s.submit('OPERATOR_UI')['status']=='START_BLOCKED_ORPHAN'
    assert len(json.loads((tmp_path/'fixture-runs.json').read_text()))==1

def test_real_windows_ui_restart_browser_close_and_duplicate_contract(tmp_path):
    """Actual UI, browser and worker OS processes; production DB/network untouched."""
    import urllib.request,urllib.error
    from playwright.sync_api import sync_playwright
    s=fixture_service(tmp_path)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        try:return json.load(opener.open(base+path,timeout=2))
        except OSError:return None
    def start_ui():
        log=(tmp_path/'ui.log').open('ab')
        proc=subprocess.Popen([sys.executable,'-m','scripts.qa_competitor_worker_ui','--runtime',str(tmp_path),'--port',str(port)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        log.close();identity=wait_for(lambda:get('/qa-identity'));return proc,identity['ui_pid']
    ui=None;pid=None;worker=None;checks={};blocked=[]
    try:
        ui,pid=start_ui()
        checks['initial_empty_persistent_status']=get('/api/competitor-monitoring/run')['active'] is None
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page()
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else (blocked.append(r.request.url),r.abort())[-1])
            page.goto(base+'/qa');page.click('#start');page.wait_for_function('document.querySelector("#result").textContent.includes("request_id")')
            started=json.loads(page.locator('#result').inner_text());checks['post_returned_without_crawl_completion']=started['status'] in ('STARTING','RUNNING')
            browser.close()
        entered=wait_for(lambda:load(tmp_path/'fixture-entered.json'))
        active=get('/api/competitor-monitoring/run');run=active['active']['run_id'];worker=active['active']['worker_identity']
        assert worker['pid']!=pid and worker['pid']!=os.getpid()
        if os.name=='nt':
            ui_job=get('/qa-identity')['ui_job_members']
            checks['worker_outside_UI_owned_job']=ui_job is not None and pid in ui_job and worker['pid'] not in ui_job
        checks['browser_close_survival']=liveness(worker)=='ALIVE'
        request=urllib.request.Request(base+'/competitor-monitoring/runs',data=b'source=metalmaster&max_pages=1',headers={'Origin':base,'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as err:opener.open(request)
        checks['second_UI_rejected']=err.value.code==409
        cli=subprocess.run([sys.executable,'-m','scripts.qa_competitor_worker_cli','--runtime',str(tmp_path),'--origin','CLI_MANUAL'],capture_output=True,text=True,timeout=10)
        checks['CLI_duplicate_blocked']=cli.returncode==3 and json.loads(cli.stdout)['status']=='SKIP_ALREADY_RUNNING'
        cfg=json.loads((tmp_path/'schedule.json').read_text());cfg['enabled']=True;(tmp_path/'schedule.json').write_text(json.dumps(cfg))
        scheduled=subprocess.run([sys.executable,'-m','scripts.qa_competitor_worker_cli','--runtime',str(tmp_path),'--origin','SCHEDULED'],capture_output=True,text=True,timeout=10)
        checks['scheduled_fixture_duplicate_blocked']=scheduled.returncode==3 and json.loads(scheduled.stdout)['status']=='SKIP_ALREADY_RUNNING'
        cfg['enabled']=False;(tmp_path/'schedule.json').write_text(json.dumps(cfg))
        old_ui_pid=pid;os.kill(pid,signal.SIGTERM);ui.wait(timeout=10);ui=None;pid=None
        checks['worker_survived_UI_stop']=liveness(worker)=='ALIVE'
        ui,pid=start_ui();reconnected=get('/api/competitor-monitoring/run')
        checks['UI_reconnected_to_same_run']=reconnected['active']['run_id']==run and reconnected['active']['worker_identity']==worker and reconnected['running']
        (tmp_path/'release-fixture').write_text('release')
        terminal=wait_for(lambda:(r:=get('/api/competitor-monitoring/run')) and r['active']['status']=='COMPLETED' and r)
        wait_for(lambda:liveness(worker)!='ALIVE')
        checks['terminal_run_persisted']=terminal['active']['run_id']==run and bool(terminal['active']['finished_at'])
        rows=json.loads((tmp_path/'fixture-runs.json').read_text());checks['exactly_one_run']=len(rows)==1 and rows[0]['status']=='COMPLETED'
        checks['new_service_no_memory_required']=fixture_service(tmp_path).view()['active']['run_id']==run
        checks['external_requests_zero']=not blocked
        checks['scheduler_stays_disabled']=s.view()['config']['enabled'] is False
        evidence={'platform':sys.platform,'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'run_id':run,'worker_identity':worker,'old_UI_PID':old_ui_pid,'new_UI_PID':pid,'NETWORK_GETS':0,'fixture_state':rows}
        (tmp_path/'OS_ACCEPTANCE.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        assert all(checks.values()),checks
    finally:
        (tmp_path/'release-fixture').write_text('cleanup')
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
        if ui:
            try:ui.wait(timeout=8)
            except subprocess.TimeoutExpired:ui.terminate()
        if worker:wait_for(lambda:liveness(worker)!='ALIVE')
