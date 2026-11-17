from copy import deepcopy
import pytest
from tests.test_execution_canary import sample
from universal_supplier.control_plane import execution_canary as canary
from universal_supplier.control_plane.supplier_automation import digest


def minimal(monkeypatch):
    value=sample()
    value['purpose']=canary.VERIFICATION_PURPOSE
    for rule in value['suppliers'].values():
        rule['targets']=rule['targets'][:1]
        rule['limits']={'REFRESH':len(rule['targets']),'DISCOVERY':0}
    value['scope_sha256']=digest([e for r in value['suppliers'].values() for e in r['targets']])
    monkeypatch.setattr(canary,'prior_attempt_urls',lambda value:{'https://partner-st.ru/previous-uncertain/'})
    return value


def test_minimal_grant_uses_native_canary_guards(monkeypatch):
    value=minimal(monkeypatch)
    canary.validate(value)
    assert canary.is_canary(value)


def test_old_exact_twenty_each_stays_strict(monkeypatch):
    value=minimal(monkeypatch);value['purpose']=canary.PURPOSE
    with pytest.raises(PermissionError,match='EXACT_20'):canary.validate(value)


@pytest.mark.parametrize('change',['retry','apply','resume','old_url','extra_url','held','missing_source'])
def test_minimal_verification_never_relaxes_boundaries(monkeypatch,change):
    v=minimal(monkeypatch);r=v['suppliers']['partner_st']
    if change=='retry':r['policy']['max_attempts']=2
    if change=='apply':v['matching_apply']=True
    if change=='resume':v['explicit_safe_resume']=True
    if change=='old_url':r['targets'][0]['source_url']='https://partner-st.ru/previous-uncertain/'
    if change=='extra_url':r['targets'].append(deepcopy(r['targets'][0]))
    if change=='held':r['targets'][0]['source_product_id']=15
    if change=='missing_source':v['suppliers'].pop('intervesp')
    with pytest.raises(PermissionError):canary.validate(v)


def test_missing_frozen_checkpoint_pins_fail_closed():
    with pytest.raises(PermissionError,match='PRIOR_PINS_REQUIRED'):
        canary.prior_attempt_urls({})
