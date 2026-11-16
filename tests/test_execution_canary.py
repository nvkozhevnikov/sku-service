"""Purpose boundaries; no HTTP or PostgreSQL required."""
from copy import deepcopy
import pytest
from tests.test_automation_execution import grant
from universal_supplier.control_plane.execution_canary import validate,PURPOSE,LIVE
from universal_supplier.control_plane.supplier_automation import digest
from universal_supplier.control_plane import commercial_refresh as refresh

def sample():
    value=grant();value.update(purpose=PURPOSE,matching_apply=False,commercial_only=True,discovery=False,explicit_safe_resume=False)
    for code,r in value['suppliers'].items():
        r.update(targets=[],settings_before={'enabled':True},settings_after={'enabled':True},policy={'max_attempts':1},max_queue_attempts=1)
        if code in LIVE:
            host={'partner_st':'partner-st.ru','optimum':'optimum.su'}[code]
            r['targets']=[dict(supplier_code=code,supplier_id=r['supplier_id'],source_product_id=r['supplier_id']*100+i,external_id=str(i),source_url='https://'+host+'/catalog/qa'+str(i)+'/',confirmed_sterbrust_id='123' if code=='partner_st' else None) for i in range(20)]
        r['limits']={'REFRESH':len(r['targets']),'DISCOVERY':0}
    value['scope_sha256']=digest([e for r in value['suppliers'].values() for e in r['targets']])
    return value

def test_only_two_sources_and_forty_urls():validate(sample())

@pytest.mark.parametrize('field,value',[('matching_apply',True),('explicit_safe_resume',True),('continuation',{}),('discovery',True)])
def test_no_expansion(field,value):
    v=sample();v[field]=value
    with pytest.raises(PermissionError):validate(v)

@pytest.mark.parametrize('change',['extra_url','duplicate','disabled','retry','robots_exception','saved_change','unconfirmed_partner'])
def test_strict_native_scope(change):
    v=sample();r=v['suppliers']['partner_st']
    if change=='extra_url':r['targets'].append(deepcopy(r['targets'][0]))
    if change=='duplicate':r['targets'][1]['source_url']=r['targets'][0]['source_url']
    if change=='disabled':r['settings_before']['enabled']=False
    if change=='retry':r['policy']['max_attempts']=2
    if change=='robots_exception':r['robots_policy_exception']={'x':1}
    if change=='saved_change':v['suppliers']['intervesp']['settings_after']['enabled']=False
    if change=='unconfirmed_partner':
        for e in r['targets']:e['confirmed_sterbrust_id']=None
    with pytest.raises(PermissionError):validate(v)

def test_legacy_1270_constraint_not_relaxed():
    v=sample();v['purpose']=refresh.PURPOSE
    with pytest.raises(PermissionError,match='EXACT_1270'):refresh.validate(v)

def test_consumed_grant_never_resumes():
    from universal_supplier.control_plane.execution_canary import require_unused
    require_unused(set())
    with pytest.raises(PermissionError,match='ALREADY_CONSUMED'):require_unused({42})

def test_matching_apply_environment_drift_fails_closed(monkeypatch):
    from universal_supplier.control_plane.source_backend import SourceBackend
    backend=object.__new__(SourceBackend);backend.release_canary=True
    monkeypatch.setenv('STERBRUST_RC_MATCHING_APPLY_APPROVED','YES')
    with pytest.raises(PermissionError,match='APPLY_FORBIDDEN'):backend.matching(['1'])

@pytest.mark.parametrize('field,value',[('external_id','other'),('canonical_url','https://partner-st.ru/elsewhere/')])
def test_native_card_cannot_escape_signed_ids_or_url(monkeypatch,field,value):
    from types import SimpleNamespace
    from universal_supplier.control_plane import execution_canary as canary
    v=sample();entry=v['suppliers']['partner_st']['targets'][0]
    monkeypatch.setattr('universal_supplier.control_plane.automation_execution.require_active',lambda:SimpleNamespace(value=v))
    card=SimpleNamespace(supplier_code='partner_st',external_id=entry['external_id'],canonical_url=entry['source_url'])
    capture=SimpleNamespace(requested_url=entry['source_url'],final_url=entry['source_url'])
    assert canary.authorize_native(card,capture)==entry
    setattr(card,field,value)
    with pytest.raises(PermissionError,match='OUTSIDE_SCOPE'):canary.authorize_native(card,capture)
