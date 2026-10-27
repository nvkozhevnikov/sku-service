"""Proof rejection on real saved137/138 fixtures; SQL and HTTP forbidden."""
from pathlib import Path
from copy import deepcopy
import gzip,json
import pytest
from universal_supplier.control_plane.commercial_continuation import check_files
from universal_supplier.control_plane.source_resume import digest

F=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/commercial_network_incident_20261027.json.gz').read_bytes()))

@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def denied(*a,**k):raise AssertionError('NO_LIVE_OR_SQL')
    monkeypatch.setattr('psycopg.connect',denied);monkeypatch.setattr('urllib.request.OpenerDirector.open',denied)


def saved(tmp_path,code,change=None):
    case=deepcopy(F['cases'][code])
    if change:change(case)
    cp=case['checkpoint'];cp.pop('checksum');cp['checksum']=digest(cp)
    for name in ('input','checkpoint','result'):(tmp_path/(name+'.json')).write_text(json.dumps(case[name]),encoding='utf-8')
    return case


@pytest.mark.parametrize('code',['beka_mak','intervesp'])
def test_actual_zero_product_attempt_is_safe_for_new_exact_frozen_inputs(tmp_path,code):
    c=saved(tmp_path,code)
    p=check_files(tmp_path,code,c['job_id'],c['result'],c['input']['entries'],F['previous_engine'])
    assert len(p['checkpoint_sha256'])==64 and p['input_sha256']==c['result']['input_sha256']
    assert c['result']['canary']['product_requests']==[]
    assert c['result']['sql_committed']==0 and c['checkpoint']['terminal']=={}
    assert len(c['checkpoint']['frontier']) in (14,320)


@pytest.mark.parametrize('state',['COMMITTED','HTTP_CAPTURED_NOT_COMMITTED','UNCERTAIN','QUARANTINED'])
def test_any_existing_product_operation_rejects_zero_product_continuation(tmp_path,state):
    def change(c):
        url=c['checkpoint']['frontier'].pop();c['checkpoint']['terminal'][url]={'state':state,'receipt':{'state':state,'url':url}}
    c=saved(tmp_path,'beka_mak',change)
    with pytest.raises(PermissionError,match='NOT_PROVEN_UNREQUESTED'):
        check_files(tmp_path,'beka_mak',137,c['result'],c['input']['entries'],F['previous_engine'])


@pytest.mark.parametrize('change,reason',[
    (lambda c:c['checkpoint']['inflight'].update({'url':{}}),'NOT_PROVEN_UNREQUESTED'),
    (lambda c:c['result']['canary']['product_requests'].append('already-dispatched'),'ALREADY_ATTEMPTED'),
    (lambda c:c['result'].update(sql_committed=1),'ALREADY_ATTEMPTED'),
    (lambda c:c['input']['entries'][0].update(url='https://beka-mak.su/product/outside/'),'PREVIOUS_ENGINE_OR_INPUT'),
])
def test_receipt_scope_or_dispatch_drift_stops_without_any_io(tmp_path,change,reason):
    c=saved(tmp_path,'beka_mak',change)
    with pytest.raises(PermissionError,match=reason):
        check_files(tmp_path,'beka_mak',137,c['result'],c['input']['entries'],F['previous_engine'])
