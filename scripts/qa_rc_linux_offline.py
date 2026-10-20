"""Explicit Linux3.12 gate. Refuses Windows; never contacts HTTP or PostgreSQL."""
from pathlib import Path
import os,sys,json,platform,socket

def main():
    if platform.system()!='Linux' or sys.version_info[:2]!=(3,12):
        raise SystemExit('BLOCKED: this gate requires an actual Linux kernel and CPython3.12')
    root=Path(__file__).resolve().parents[1];os.chdir(root);sys.path.insert(0,str(root))
    for key in list(os.environ):
        if key.startswith(('DB_','PG','COMPETITOR_')):os.environ.pop(key)
    os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
    for key in ('STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):
        os.environ.pop(key,None)
    def denied(*args,**kwargs):raise AssertionError('LINUX_OFFLINE_GATE_NETWORK_OR_DB_FORBIDDEN')
    socket.socket.connect=denied;socket.create_connection=denied
    import psycopg
    psycopg.connect=denied
    import pytest
    print(json.dumps({'platform':platform.platform(),'python':sys.version,'network':'DENIED','postgres':'DENIED'}),flush=True)
    return pytest.main(['tests/test_automatic_matching.py','tests/test_source_jobs.py','tests/test_f01_f03_identity_inputs.py','tests/test_matching.py','tests/test_unified_replay_dispatch.py','tests/test_rc_recovered_source_status.py','tests/test_service_idempotency.py','tests/test_partner_history_fix.py','tests/test_partner_pilot.py','tests/test_readiness_panel.py','tests/test_matching_load_scope.py','tests/test_host_fetch.py','tests/test_source_resume.py','tests/test_stage6c.py','-q','-p','no:cacheprovider',*sys.argv[1:]])

if __name__=='__main__':raise SystemExit(main())
