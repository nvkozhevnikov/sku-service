"""A detached host must log admission failures and never start another loop."""
from pathlib import Path
import importlib.util,json
import pytest

# The host is a Windows process wrapper over the operator's existing audit venv
# (scripts/one_shot_supplier_host.py VENV_SITE); elsewhere it refuses to import.
if not (Path(__file__).resolve().parents[2]/'sku-service/.venv-audit/Lib/site-packages').is_dir():
    pytest.skip('operator Windows runtime (sku-service/.venv-audit) not present', allow_module_level=True)
spec=importlib.util.spec_from_file_location('one_shot_host',Path(__file__).parents[1]/'scripts/one_shot_supplier_host.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)


@pytest.mark.parametrize('in_job,reason',[(True,'INDEPENDENT_PROCESS_HOST_NOT_CONFIRMED'),(False,'AUTOMATION_EXACT_OPERATOR_CONFIRMATION_REQUIRED')])
def test_host_records_job_or_admission_failure_before_queue_http_sql(tmp_path,monkeypatch,in_job,reason):
    monkeypatch.setattr(host,'ROOT',tmp_path)
    monkeypatch.setattr(host,'process_in_job',lambda:in_job)
    for flag in ('STERBRUST_LIVE11_RECOVERY_FILE','STERBRUST_LIVE11_AUTONOMOUS_SESSION'):monkeypatch.delenv(flag,raising=False)
    def deny(*a,**k):raise AssertionError('NO_NETWORK_OR_SQL')
    monkeypatch.setattr('psycopg.connect',deny);monkeypatch.setattr('urllib.request.OpenerDirector.open',deny)
    def gate(*a,**kw):
        assert not in_job and kw['for_execution'] is True
        raise PermissionError(reason)
    monkeypatch.setattr('universal_supplier.control_plane.automation_execution.load',gate)
    output=tmp_path/'reports/automation-launch/test'
    assert host.main(['--output',str(output),'--approval','grant.json','--sha256','a'*64])==1
    state=json.loads((output/'HOST.json').read_text())
    assert state['stage']=='STOPPED' and state['no_retry'] and state['no_task_or_cron']
    assert reason in json.dumps(state)


def test_probe_has_no_database_approval_loader_or_worker(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'ROOT',tmp_path);monkeypatch.setattr(host,'process_in_job',lambda:False)
    monkeypatch.setattr(host.time,'sleep',lambda seconds:None)
    def deny(*a,**k):raise AssertionError('PROBE_MUST_NOT_LOAD_OR_CONNECT')
    monkeypatch.setattr('universal_supplier.control_plane.automation_execution.load',deny)
    monkeypatch.setattr('psycopg.connect',deny)
    output=tmp_path/'reports/automation-launch/probe'
    assert host.main(['--output',str(output),'--probe'])==0
    state=json.loads((output/'HOST.json').read_text())
    assert state['stage']=='PROBE_COMPLETE' and state['HTTP']==state['SQL']==state['enqueue']==0 and not state['in_job']
