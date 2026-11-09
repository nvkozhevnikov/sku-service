"""Offline guards for the signed 5 s re-sign path; SQL and HTTP forbidden."""
from copy import deepcopy
from pathlib import Path
import gzip,json,hashlib
import pytest
from universal_supplier.control_plane import commercial_continuation as cc
from universal_supplier.control_plane.commercial_refresh import matrix_pins

F=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/commercial_network_incident_20261027.json.gz').read_bytes()))


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def denied(*a,**k):raise AssertionError('NO_LIVE_OR_SQL')
    monkeypatch.setattr('psycopg.connect',denied);monkeypatch.setattr('urllib.request.OpenerDirector.open',denied)


def saved(tmp_path,reason,stop_scope):
    case=deepcopy(F['cases']['intervesp'])
    case['result']['termination_reason']=reason
    case['result']['stop_scope']=stop_scope
    from universal_supplier.control_plane.source_resume import digest
    cp=case['checkpoint'];cp.pop('checksum');cp['checksum']=digest(cp)
    for name in ('input','checkpoint','result'):(tmp_path/(name+'.json')).write_text(json.dumps(case[name]),encoding='utf-8')
    return case


def test_robots_slower_profile_stop_proves_not_requested(tmp_path):
    case=saved(tmp_path,'ROBOTS_REQUIRES_SLOWER_PROFILE','supplier')
    record=cc.check_receipt_files(tmp_path,'intervesp',case['job_id'],case['result']['crawl_run_id'],
        'partial',case['result'],case['input']['entries'],F['previous_engine'])
    assert record['disposition']=='NOT_REQUESTED' and record['committed']==0 and record['quarantined']==0


def test_robots_slower_profile_without_source_local_scope_rejects(tmp_path):
    case=saved(tmp_path,'ROBOTS_REQUIRES_SLOWER_PROFILE',None)
    with pytest.raises(PermissionError,match='CONTINUATION_ROBOTS_NOT_SOURCE_LOCAL'):
        cc.check_receipt_files(tmp_path,'intervesp',case['job_id'],case['result']['crawl_run_id'],
            'partial',case['result'],case['input']['entries'],F['previous_engine'])


def test_unknown_partial_reason_is_never_not_requested(tmp_path):
    case=saved(tmp_path,'SOURCE_CERTIFICATE_INVALID','supplier')
    with pytest.raises(PermissionError,match='CONTINUATION_PHASE_NOT_ACCOUNTED'):
        cc.check_receipt_files(tmp_path,'intervesp',case['job_id'],case['result']['crawl_run_id'],
            'partial',case['result'],case['input']['entries'],F['previous_engine'])


def contract(tmp_path,change=None,mutate_value=None):
    old={'scope_sha256':'s','target':{'pgdata':'x'},'mappings1282':[[1,2]],'protected_live11_batch_id':'b',
        'not_before':'2026-10-27T13:00:00Z','execution_until':'2026-10-28T13:00:00Z',
        'suppliers':{'kami':{'policy':{'min_request_interval_seconds':0.2},'rate_sha256':'k1','settings_sha256':'k2',
            'settings_after':{'enabled':True},'settings_before':{'enabled':True},'min_request_interval_seconds':0.2,
            'targets':[1],'limits':{'REFRESH':1,'DISCOVERY':0},'supplier_id':4,'host':'kami.ru',
            'max_queue_attempts':3,'max_run_duration_seconds':7200,'max_discovery_pages':1,'enabled_at_approval':True},
            'intervesp':{'policy':{'min_request_interval_seconds':1.0},'rate_sha256':'i1','settings_sha256':'i2',
            'settings_after':{'enabled':True,'rate_settings':{'min_request_interval_seconds':1.0}},
            'settings_before':{'enabled':True},'min_request_interval_seconds':1.0,
            'targets':[7,8],'limits':{'REFRESH':2,'DISCOVERY':0},'supplier_id':13,'host':'intervesp.ru',
            'max_queue_attempts':3,'max_run_duration_seconds':14400,'max_discovery_pages':1,'enabled_at_approval':True}},
        'host_policies':{'kami.ru':{'kami':'kd'},'intervesp.ru':{'intervesp':'id1'}}}
    if change:change(old)
    path=tmp_path/'old.json';path.write_text(json.dumps(old),encoding='utf-8')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    p={'kind':'commercial-receipt-continuation/v1','previous_path':str(path),'previous_approval_sha256':sha,
        'protected_jobs':[[9,'x']],
        'jobs':[{'source':'kami','disposition':'REUSE_TERMINAL'},{'source':'intervesp','disposition':'NOT_REQUESTED'}]}
    value=deepcopy(old);value['continuation']=p
    value['operation_id']=sha;value['protected_jobs']=[[9,'x']]
    if mutate_value:mutate_value(value,p)
    return value,p


def resign_intervesp(value):
    rule=value['suppliers']['intervesp']
    rule['policy']={'min_request_interval_seconds':5.0};rule['rate_sha256']='i1b';rule['settings_sha256']='i2b'
    rule['settings_after']=dict(rule['settings_after'],rate_settings={'min_request_interval_seconds':5.0})
    rule['min_request_interval_seconds']=5.0
    rule['robots_policy_exception']={'format':'robots-policy-exception/v1','source':'intervesp','host':'intervesp.ru',
        'robots_sha256':'a'*64,'declared_minimum_seconds':20.0,'applied_min_request_interval_seconds':5.0,
        'requires_operator_confirmation':True}
    value['host_policies']['intervesp.ru']['intervesp']='id5'


@pytest.fixture
def patched_lineage(monkeypatch):
    monkeypatch.setattr(cc,'validate_window',lambda *a,**k:None)
    holder=[]
    monkeypatch.setattr(cc,'receipt_proof',lambda config,path,sha,**k:holder[0] if holder else None)
    return holder


def test_not_requested_source_may_resign_only_pacing(tmp_path,patched_lineage):
    value,p=contract(tmp_path);patched_lineage.append(p);resign_intervesp(value)
    assert cc.validate_live(None,value) is None


def test_reuse_terminal_source_stays_frozen(tmp_path,patched_lineage):
    def mutate(value,p):resign_intervesp(value);value['suppliers']['kami']['policy']={'min_request_interval_seconds':0.3}
    value,p=contract(tmp_path,mutate_value=mutate);patched_lineage.append(p)
    with pytest.raises(PermissionError,match='CONTINUATION_FROZEN_SOURCE_CONTRACT_CHANGED'):
        cc.validate_live(None,value)


def test_not_requested_scope_cannot_change(tmp_path,patched_lineage):
    def mutate(value,p):resign_intervesp(value);value['suppliers']['intervesp']['targets']=[7,8,9]
    value,p=contract(tmp_path,mutate_value=mutate);patched_lineage.append(p)
    with pytest.raises(PermissionError,match='CONTINUATION_NOT_REQUESTED_SCOPE_CHANGED'):
        cc.validate_live(None,value)


def test_frozen_host_digest_cannot_change(tmp_path,patched_lineage):
    def mutate(value,p):resign_intervesp(value);value['host_policies']['kami.ru']['kami']='kd2'
    value,p=contract(tmp_path,mutate_value=mutate);patched_lineage.append(p)
    with pytest.raises(PermissionError,match='CONTINUATION_FROZEN_HOST_POLICY_CHANGED'):
        cc.validate_live(None,value)


def git(monkeypatch,changed,merge_base=0,quiet=0):
    monkeypatch.setattr(cc.subprocess,'check_output',lambda *a,**k:'\n'.join(changed))
    monkeypatch.setattr(cc.subprocess,'call',lambda *a,**k:quiet if a[0][:1]==['git'] and a[0][1]=='diff' else merge_base)


def test_pinned_robots_exception_engine_change_is_compatible(monkeypatch):
    git(monkeypatch,['universal_supplier/control_plane/source_runtime.py'])
    out=cc.compatibility({'head':'x'})
    assert 'universal_supplier/control_plane/source_runtime.py' in out


def test_drifted_pinned_engine_change_requires_review(monkeypatch):
    git(monkeypatch,['universal_supplier/control_plane/source_runtime.py'],quiet=1)
    with pytest.raises(PermissionError,match='CONTINUATION_PINNED_ROBOTS_EXCEPTION_CODE_CHANGED'):
        cc.compatibility({'head':'x'})


def test_any_other_engine_change_requires_review(monkeypatch):
    git(monkeypatch,['universal_supplier/control_plane/orchestrator.py'])
    with pytest.raises(PermissionError,match='CONTINUATION_ENGINE_COMPATIBILITY_REQUIRES_REVIEW'):
        cc.compatibility({'head':'x'})


def matrix(tmp_path,rows):
    path=tmp_path/'m.csv';path.write_bytes(('SOURCE,APPROVAL_DIGEST_SHA256\r\n'+''.join(f'{s},{d}\r\n' for s,d in rows)).encode('utf-8-sig'))
    return path


def mvalue(matrix_path,**over):
    value={'matrix_sha256':'0'*64,'suppliers':{'kami':{'host':'kami.ru'},'intervesp':{'host':'intervesp.ru'}},
        'host_policies':{'kami.ru':{'kami':'kd'},'intervesp.ru':{'intervesp':'id1'}},
        'continuation':{'kind':'commercial-receipt-continuation/v1',
            'jobs':[{'source':'kami','disposition':'REUSE_TERMINAL'},{'source':'intervesp','disposition':'NOT_REQUESTED'}]}}
    value.update(over);return value


def test_matrix_exact_pin_still_accepted(tmp_path):
    path=matrix(tmp_path,[('kami','kd'),('intervesp','id1')])
    value=mvalue(path,matrix_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),continuation={})
    assert matrix_pins(value,path) is None


def test_matrix_row_drift_confined_to_not_requested_accepted(tmp_path):
    path=matrix(tmp_path,[('kami','kd'),('intervesp','id5')])
    assert matrix_pins(mvalue(path),path) is None


def test_matrix_drift_of_frozen_row_rejects(tmp_path):
    path=matrix(tmp_path,[('kami','kd2'),('intervesp','id5')])
    with pytest.raises(PermissionError,match='COMMERCIAL_REFRESH_MATRIX_CHANGED'):
        matrix_pins(mvalue(path),path)


def test_matrix_drift_without_not_requested_source_rejects(tmp_path):
    path=matrix(tmp_path,[('kami','kd2'),('intervesp','id1')])
    value=mvalue(path,continuation={'kind':'commercial-receipt-continuation/v1',
        'jobs':[{'source':'kami','disposition':'REUSE_TERMINAL'}]})
    with pytest.raises(PermissionError,match='COMMERCIAL_REFRESH_MATRIX_CHANGED'):
        matrix_pins(value,path)
