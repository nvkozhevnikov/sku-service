"""Proposal validation never dispatches HTTP, SQL writes or a worker."""
from copy import deepcopy
import pytest
from universal_supplier.control_plane.release_canary import validate,FORMAT,TARGET
from universal_supplier.control_plane.source_jobs import RUNTIME_SOURCES
from universal_supplier.control_plane.supplier_automation import digest

def sample():
    return dict(format=FORMAT,execution_authorized=False,target=dict(TARGET),head='1'*40,engine='2'*64,
        mappings1282_sha256='3'*64,matching_apply=False,discovery=False,production=False,esol=False,
        identity_rule='stable_internal_id_only_no_slug_title_merge',product_url_count=0,
        sources={code:dict(mode='SAVED_ONLY',supplier_enabled=False,settings_sha256='4'*64,
            entries=[],entries_sha256=digest([])) for code in RUNTIME_SOURCES})

def test_proposal_only():assert validate(sample())['execution_authorized'] is False

@pytest.mark.parametrize('field,value',[('execution_authorized',True),('matching_apply',True),
    ('discovery',True),('production',True),('esol',True),('product_url_count',221),
    ('head','unknown'),('engine','changed'),('mappings1282_sha256','unknown'),('identity_rule','slug')])
def test_no_scope_expansion(field,value):
    item=sample();item[field]=value
    with pytest.raises(ValueError):validate(item)

@pytest.mark.parametrize('code',['intervesp','beka_mak_tr'])
def test_mixed_and_reference_never_live(code):
    item=sample();item['sources'][code].update(mode='LIVE_PROPOSED',supplier_enabled=True)
    with pytest.raises(ValueError):validate(item)

def test_disabled_supplier_not_activated():
    item=sample();item['sources']['partner_st']['mode']='LIVE_PROPOSED'
    with pytest.raises(ValueError):validate(item)

def test_missing_source_is_not_a_smaller_approved_grant():
    item=sample();item['sources'].pop('intervesp')
    with pytest.raises(ValueError):validate(item)

def test_over20_rejected_before_any_url_handling():
    item=sample();entries=[{}]*21
    item['sources']['partner_st'].update(entries=entries,entries_sha256=digest(entries));item['product_url_count']=21
    with pytest.raises(ValueError):validate(item)

def test_duplicate_url_not_requested_twice():
    item=sample();entries=[dict(source_product_id=i,stable_external_id=str(i),url='https://partner-st.ru/product/qa/') for i in (1,2)]
    item['sources']['partner_st'].update(entries=entries,entries_sha256=digest(entries));item['product_url_count']=2
    with pytest.raises(ValueError):validate(item)
