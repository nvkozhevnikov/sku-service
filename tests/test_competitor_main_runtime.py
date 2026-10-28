from pathlib import Path
import pytest
from psycopg.conninfo import make_conninfo
from competitor_intel.store import Store

def test_main_role_is_explicit_and_owner_rejected(monkeypatch):
    monkeypatch.setenv('STERBRUST_UNIFIED_RC','YES');monkeypatch.setenv('STERBRUST_COMPETITOR_MAIN','YES')
    for db,user in [('rc_verify_vi_20261019','rc_owner'),('sterbrust_unified_rc_20261019','competitor_runtime')]:
        with pytest.raises(ValueError,match='identity mismatch'):
            Store(make_conninfo(host='127.0.0.1',port=55461,dbname=db,user=user))

def test_main_not_enabled_by_dsn_only(monkeypatch):
    monkeypatch.delenv('STERBRUST_UNIFIED_RC',raising=False);monkeypatch.delenv('STERBRUST_COMPETITOR_MAIN',raising=False)
    with pytest.raises(ValueError,match='dedicated'):
        Store(make_conninfo(host='127.0.0.1',port=55461,dbname='rc_verify_vi_20261019',user='competitor_runtime'))

def test_permanent_runtime_clears_rehearsal_and_uses_own_config(monkeypatch):
    from competitor_intel.main_runtime import configure,services,CONFIG,RUNTIME
    monkeypatch.setenv('STERBRUST_COMPETITOR_REHEARSAL','YES')
    monkeypatch.setenv('STERBRUST_UNIFIED_RC','NO');monkeypatch.setenv('STERBRUST_COMPETITOR_MAIN','NO')
    monkeypatch.setenv('PGCONNECT_TIMEOUT','5');configure();import os
    assert 'STERBRUST_COMPETITOR_REHEARSAL' not in os.environ
    _,collection=services()
    assert Path(collection.dev_config)==CONFIG and collection.runtime==RUNTIME
    assert 'sku-service-autonomous-system-20261026' in str(CONFIG)

def test_previous_source_partial_remains_visible_after_other_source_canary():
    from tests.test_competitor_monitoring_ui import fixture_state,service,client
    state=fixture_state()
    state['competitor_runs']=[{'id':60,'result':{'sources':{'fixture_auto':{'status':'PARTIAL','not_fetched_due_to_bound':341}}}},{'id':61,'result':{'sources':{'other_source':{'status':'COMPLETED'}}}}]
    projected=service(state).view('sources',{})
    row=next(s for s in projected['sources'] if s['code']=='fixture_auto')
    assert row['last_collection_run']==60 and row['last_collection_outcome']['status']=='PARTIAL'
    with client(service(state)) as c:
        html=c.get('/competitor-monitoring/sources').text
        assert 'Последний сбор: PARTIAL' in html and 'Run 60' in html and '341' in html

def test_bound_stop_preserves_current_source_and_skips_next(tmp_path):
    from competitor_intel.execution import settings,run_competitor_collection,atomic_json,execution_lock
    from tests.test_competitor_execution import Repo
    cfg=settings();cfg['selected_sources']=['metalmaster','jet_online'];calls=[]
    def collect(repo,source,run,*args):
        calls.append(source)
        journal=__import__('json').loads((tmp_path/'last-run.json').read_text())
        atomic_json(tmp_path/'stop-request.json',{'run_id':run,'worker_identity':journal['worker_identity']})
        return {'status':'COMPLETED','stored':1}
    result=run_competitor_collection(Repo(),collect,lambda *a:{},origin='CLI_MANUAL',config=cfg,runtime=tmp_path)
    assert calls==['metalmaster'] and result['status']=='PARTIAL'
    assert result['sources']['jet_online']['status']=='SKIPPED_OPERATOR_STOP'
    assert result['sources']['metalmaster']['stored']==1
    with execution_lock(tmp_path):pass

def test_stale_stop_cannot_cancel_new_run(tmp_path):
    from competitor_intel.execution import settings,run_competitor_collection,atomic_json
    from tests.test_competitor_execution import Repo
    atomic_json(tmp_path/'stop-request.json',{'run_id':-1,'worker_identity':{'pid':-1}})
    cfg=settings();cfg['selected_sources']=['metalmaster'];calls=[]
    def collect(*args):calls.append(1);return {'status':'COMPLETED','stored':1}
    result=run_competitor_collection(Repo(),collect,lambda *a:{},origin='CLI_MANUAL',config=cfg,runtime=tmp_path)
    assert result['status']=='COMPLETED' and calls==[1]

@pytest.mark.parametrize('error,exit_code',[(ValueError('private-fixture-detail'),4),(RuntimeError('private-fixture-detail'),2)])
def test_main_cli_start_failure_preserves_native_exit_codes(monkeypatch,capsys,error,exit_code):
    import runpy,sys
    from competitor_intel import main_runtime
    class FailedCollection:
        def run(self,*args):raise error
    monkeypatch.setattr(main_runtime,'configure',lambda:None)
    monkeypatch.setattr(main_runtime,'services',lambda:(None,FailedCollection()))
    monkeypatch.setattr(sys,'argv',['rc_runtime.py','--collect-competitors','--source','metalmaster','--max-pages','1'])
    script=Path(__file__).resolve().parents[1]/'scripts/rc_runtime.py'
    monkeypatch.syspath_prepend(str(script.parent))
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(script),run_name='__main__')
    assert stopped.value.code==exit_code
    output=capsys.readouterr().out
    assert 'private-fixture-detail' not in output and str(exit_code) in output
