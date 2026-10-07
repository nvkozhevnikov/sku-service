from copy import deepcopy
from xml.etree import ElementTree as ET
import pytest
from universal_supplier.operation_release import plan_operations,operation_xml,operation_manifest


def existing():
    return {'source':'kami','external_id':'fixture-price','classification':'EXISTING_CONFIRMED','name':'Machine M200X',
            'sterbrust_product_id':'123','source_product_id':1,'offer_id':2,'source_catalog_product_id':3,
            'effective_identity_evidence':{'source_product_id':1,'source_catalog_product_id':3,'catalog_product_id':3,
                'current_match_status':'EXACT_MATCH','current_match_catalog_product_id':3},
            'selection_authority':'PERSISTED_CANONICAL_SQL','selected_offer':True,'supplier_enabled':True,
            'offer_active':True,'source_active':True,'canonical_active':True,'selection_eligible_verified':True,
            'price':'120','currency':'RUB','price_type':'retail','price_state':'numeric','price_basis':'exact','product_kind':'machine'}


def current(price='100'):
    return {'123':{'price':price,'currency':'RUB','price_type':'retail','authoritative_current':True,
                   'source_ref':'pinned-current-source-sha','observed_at':'2026-10-07T00:00:00Z'}}


def full_new():
    r={'source':'kami','external_id':'fixture-new','classification':'READY_TO_CREATE_FULL','name':'Machine M300X',
       'brand':'TEST','model':'M300X','new_group_id':'NEWG-SYNTHETIC','new_candidate_id':'NEWC-SYNTHETIC',
       'readiness_authority':'OPERATOR_ACCEPTED_FULL_PROOF','source_catalog_product_id':None,'product_kind':'machine',
       'evidence_ref':'pinned-saved-source-sha'}
    for g in ('canonical_absence_verified','source_identity_verified','typed_identity_verified','section_verified','cross_source_dedup_verified','machine_kind_verified'):r[g]=True
    vals={'name':r['name'],'brand':'TEST','model_execution':'M300X','category':{'id':'5641','snapshot_active_verified':True},
          'description':'Observed source description','characteristics':[{'label':'Length','value':300,'unit':'mm'}],
          'price_state':'price_on_request','price':None,'availability':'in_stock'}
    p={'source':'kami','external_id':r['external_id'],'new_group_id':r['new_group_id'],
       'readiness':{'identity_ready':True,'full_ready':True,'blockers':[]},
       'fields':{k:{'state':'OBSERVED','value':v} for k,v in vals.items()}}
    return r,p


def test_new_existing_delta_disjoint_and_price_only():
    n,p=full_new();old=current();old['123'].update(availability='in_stock',quantity=10)
    plan=plan_operations([n,existing()],[p],old)
    assert len(plan['new'])==len(plan['prices'])==1 and plan['disjoint']
    new=ET.fromstring(operation_xml(plan,'NEW_PRODUCTS',{}));price=ET.fromstring(operation_xml(plan,'PRICE_UPDATES',{}))
    assert not any('sterbrust_product_id' in x.attrib for x in new.findall('.//Product'))
    assert price.find('.//FullCard') is None and price.find('.//SourceIdentity') is None
    text=ET.tostring(price).decode();assert 'availability' not in text and 'quantity' not in text
    assert price.find('.//Product').attrib['sterbrust_product_id']=='123'


def test_existing_unchanged_in_neither():
    plan=plan_operations([existing()],[],current('120'));assert not plan['new'] and not plan['prices']


@pytest.mark.parametrize('cls',['REVIEW','CONFLICT','NEW_CANDIDATE'])
def test_unresolved_in_neither(cls):
    r=existing();r['classification']=cls;p=plan_operations([r],[],current());assert not p['new'] and not p['prices']


@pytest.mark.parametrize('change',[
    {'sterbrust_product_id':''},{'effective_identity_evidence':None},{'selected_offer':False},
    {'price':None,'price_state':'price_on_request'},{'price':'0'},{'price':'NaN'},
    {'currency':'BAD'},{'price_type':'unknown'},{'price_basis':'from_price'},
    {'price_basis':None},
    {'offer_active':False},{'product_kind':'accessory'},{'selection_authority':'ADVISORY'},
])
def test_invalid_price_identity_or_kind_blocked(change):
    p=plan_operations([{**existing(),**change}],[],current());assert not p['prices']


def test_missing_current_sterbrust_price_not_guessed():
    assert not plan_operations([existing()],[],{})['prices']


def test_new_with_existing_id_blocked():
    n,p=full_new();n['sterbrust_product_id']='123'
    assert not plan_operations([n],[p],{})['new']


def test_advisory_cannot_change_operation_bucket():
    n,p=full_new();n['readiness_authority']='ADVISORY';assert not plan_operations([n],[p],{})['new']
    r=existing();r['advisory_classification']='REVIEW';assert len(plan_operations([r],[],current())['prices'])==1


def test_duplicate_source_no_duplicate_operation():
    r=existing();p=plan_operations([r,deepcopy(r)],[],current());assert len(p['prices'])==1
    with pytest.raises(ValueError):plan_operations([r,{**r,'price':'140'}],[],current())


def test_manual_and_negative_identity_authority():
    r=existing();r['effective_identity_evidence'].update(current_match_status='REVIEW',effective_manual_source_product_id=1,effective_manual_catalog_product_id=3)
    assert len(plan_operations([r],[],current())['prices'])==1
    r['effective_identity_evidence']['effective_manual_catalog_product_id']=4
    assert not plan_operations([r],[],current())['prices']


def test_scope_never_fills_empty_kami_from_predecessor():
    r=existing();r['source']='partner_st'
    plan=plan_operations([r],[],current());m=operation_manifest(plan,{'pinned':'sha'})
    assert all(v['count']==0 for v in m['files'].values())
    for op in ('NEW_PRODUCTS','PRICE_UPDATES'):
        first=operation_xml(plan,op,{'pinned':'sha'});assert first==operation_xml(plan,op,{'pinned':'sha'})
        assert ET.fromstring(first).attrib['count']=='0'


def test_false_match_guard_not_whitelisted():
    r=existing();r.update(source='optimum',external_id='782')
    assert not plan_operations([r],[],current(),scope=('optimum',))['prices']
