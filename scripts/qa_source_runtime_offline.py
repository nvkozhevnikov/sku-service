"""Focused collector regression with real network and PostgreSQL disabled.

Run with the project's Python environment. Optional pytest arguments are
appended to the fixed suite; artifacts remain in reports/q<timestamp>/.
Use --export-only for the independent export security regression.
"""
from pathlib import Path
from datetime import datetime
import os
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'reports' / ('q' + datetime.now().strftime('%H%M%S%f'))
ARTIFACTS.mkdir(parents=True)
os.chdir(ROOT)
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP'] = 'YES'
os.environ['CONTROL_PLANE_TEST_SNAPSHOT'] = 'fixture'
os.environ['CONTROL_PLANE_INSECURE_HTTP'] = 'YES'  # TestClient uses local HTTP.

# Windows sandbox cannot reopen pytest's owner-only temporary directories.
# Restrict this compatibility adjustment to this run's disposable evidence.
original_mkdir = os.mkdir
def evidence_mkdir(path, mode=0o777, *, dir_fd=None):
    if Path(path).resolve().is_relative_to(ARTIFACTS):
        mode = 0o777
    return original_mkdir(path, mode, dir_fd=dir_fd)
os.mkdir = evidence_mkdir

import psycopg
import requests
def deny(*args, **kwargs):
    raise AssertionError('OFFLINE_QA_NO_EXTERNAL_HTTP_OR_SQL')
psycopg.connect = deny
requests.Session.send = deny
original_connect = socket.socket.connect
original_getaddrinfo = socket.getaddrinfo
def connect(sock, address):
    if (isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1')
            and address[1] not in (5432, 55461, 55452, 55455, 55449)):
        return original_connect(sock, address)
    return deny()
def getaddrinfo(host, *args, **kwargs):
    if host in ('localhost', '127.0.0.1', '::1', None):
        return original_getaddrinfo(host, *args, **kwargs)
    return deny()
socket.socket.connect = connect
socket.getaddrinfo = getaddrinfo

import pytest
SUITE = (
    'source_runtime', 'source_runtime_controls', 'source_jobs', 'source_resume',
    'host_fetch', 'readiness_panel', 'partner_history_fix', 'partner_pilot',
    'partner_stop_diagnostics', 'service_idempotency', 'f01_f03_identity_inputs',
    'automatic_matching', 'stage6c1_control', 'stage6c1_optimum', 'stage6c1_partner',
    'native_checkpoint', 'runtime_security', 'security_review_group5',
    'commercial', 'commercial_persistence', 'optimum_source', 'http_capture',
    'matching_policy_v22',
    'export_security_adoption',
    'source_canary','authorized_collection',
)
if '--bounded-only' in sys.argv:
    sys.argv.remove('--bounded-only')
    SUITE=('authorized_collection','source_canary','source_runtime','source_runtime_controls','source_resume','source_jobs',
           'host_fetch','readiness_panel','partner_pilot','partner_stop_diagnostics','native_checkpoint',
           'service_idempotency','f01_f03_identity_inputs','automatic_matching','stage6c1_optimum','stage6c1_partner')
if '--export-only' in sys.argv:
    sys.argv.remove('--export-only')
    SUITE=('export_security_adoption',)
if '--b0-only' in sys.argv:
    sys.argv.remove('--b0-only')
    SUITE=('source_linked_update','source_runtime','source_runtime_controls','source_resume',
           'source_jobs','commercial_persistence','kami_persistence','service_idempotency',
           'f01_f03_identity_inputs','automatic_matching','native_checkpoint',
           'partner_pilot','partner_history_fix','stage6c1_optimum','stage6c1_partner')
print('Offline evidence:', ARTIFACTS, flush=True)
raise SystemExit(pytest.main([
    *[f'tests/test_{name}.py' for name in SUITE], '-q', '-p', 'no:cacheprovider',
    '--basetemp=' + str(ARTIFACTS / 'tmp'),
    '--junitxml=' + str(ARTIFACTS / 'results.xml'), *sys.argv[1:],
]))
