from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from universal_supplier.kami_matching import readiness_advisory
from universal_supplier.proposed_offer_selection import proposed_selection,persisted_selection_keys
from universal_supplier.release_export import selection_view,xml_bytes
from universal_supplier.offer_selection import OfferCandidate,OfferSelectionPolicy,evaluate_offers
from pathlib import Path

PROOF={'CHARACTERISTIC_CONTRADICTION':{'blocked':False},'CANONICAL_ABSENCE_SUPPORT':{'verified':True},
       'SECTION_EVIDENCE':{'verified':True},'CHARACTERISTIC_IDENTITY_SUPPORT':{'identity_evidence_complete':True}}
POLICY=OfferSelectionPolicy.load(Path(__file__).parents[1]/'config/offer_selection.json')
TS=datetime(2026,10,6,tzinfo=timezone.utc)

def new_row(**over):
    row={'source':'kami','external_id':'site:1','classification':'NEW_CANDIDATE','name':'Lathe ABC-200V',
         'brand':'ABC','model':'ABC-200V','product_kind':'lathe','model_role':'OWN_MODEL','card_kind':'SINGLE_PRODUCT',
         'source_identity_proof':{'verified':True,'execution_verified':True,'kind':'VISIBLE_MODEL_TYPED_ANCHORS',
             'full_model':'ABC-200V','provenance':'capture://typed','independent_anchor_count':2}}
    row.update(over);return row

@pytest.mark.parametrize('over',[
    {'classification':'REVIEW','model':''},
    {'classification':'REVIEW','source_identity_proof':{'kind':'FUZZY_ONLY'}},
    {'classification':'REVIEW','card_kind':'AMBIGUOUS'},
    {'card_kind':'SERIES_OR_GROUP_CARD'},
    {'product_kind':'accessory'}, {'product_kind':'component'},
    {'source_identity_proof':{'verified':True,'kind':'TITLE_SUBSTRING','execution_verified':False}},
    {'source_identity_proof':{'verified':True,'kind':'URL_SLUG','execution_verified':True}},
])
def test_insufficient_source_proof_has_no_NEW_identity(over):
    result=readiness_advisory(new_row(**over),PROOF,dedup_verified=True,proposed_group_id='NEWG-proven')
    assert result['classification']=='REVIEW'
    assert not result.get('new_candidate_id') and not result.get('new_group_id')
    assert result['readiness']['identity_ready'] is False
    assert result['readiness']['new_group_id'] is None

def test_explicit_machine_can_reach_readiness_and_group_only_after_PASS():
    row=new_row();original=deepcopy(row)
    blocked=readiness_advisory(row,{**PROOF,'SECTION_EVIDENCE':{}},dedup_verified=True,proposed_group_id='NEWG-proven')
    assert not blocked.get('new_group_id') and not blocked.get('new_candidate_id')
    ready=readiness_advisory(row,PROOF,dedup_verified=True,proposed_group_id='NEWG-proven')
    assert ready['readiness']['state']=='READY_TO_CREATE_IDENTITY'
    assert ready['new_group_id']=='NEWG-proven' and ready['new_candidate_id']
    assert readiness_advisory(row,PROOF,dedup_verified=True,proposed_group_id='NEWG-proven')==ready
    assert row==original and not ready.get('sterbrust_product_id')

@pytest.mark.parametrize('cls',['EXISTING_CONFIRMED','CONFLICT'])
def test_existing_conflict_never_NEW(cls):
    result=readiness_advisory(new_row(classification=cls,new_candidate_id='stale',new_group_id='stale'),PROOF,
                              dedup_verified=True,proposed_group_id='NEWG-proven')
    assert result['classification']==cls and 'new_group_id' not in result and 'new_candidate_id' not in result

def offer_row(source='kami',id=1,**over):
    c=OfferCandidate(7,id,id,50,50,source,True,7,True,0,TS,True,'default',0,TS,TS,
                     Decimal('100'),'RUB','in_stock','В наличии',None)
    candidate_changes={key:value for key,value in over.items() if key in asdict(c)}
    c=replace(c,**candidate_changes)
    row={'source':source,'external_id':str(id),'classification':over.get('classification','EXISTING_CONFIRMED'),
         'sterbrust_product_id':'19870','source_product_id':c.source_product_id,'offer_id':c.offer_id,
         'price':str(c.price) if c.price is not None else None,'price_state':over.get('price_state','numeric_public'),
         'price_basis':over.get('price_basis','exact_price'),'currency':c.currency,'availability':c.availability,
         'supplier_enabled':c.supplier_enabled,'offer_active':c.offer_active,
         'selection_context':{'canonical_identity_verified':True,'sterbrust_product_id':'19870','candidate':asdict(c)}}
    return row,c

@pytest.mark.parametrize('over',[{'supplier_enabled':False},{'offer_active':False},
                                {'supplier_enabled':False,'offer_active':False}])
def test_disabled_inactive_never_proposed_winner(over):
    row,c=offer_row(**over)
    assert selection_view([row])==set()
    state=proposed_selection([row]); assert state['overall_status']=='NO_ELIGIBLE_OFFER'
    assert evaluate_offers(7,[c],POLICY).selected_offer_id is None

def test_eligible_beats_disabled_higher_rank_and_cheaper_inactive():
    active,_=offer_row('partner_st',1,price=Decimal('200'))
    disabled,_=offer_row('top',2,price=Decimal('1'),supplier_enabled=False)
    inactive,_=offer_row('cheap',3,price=Decimal('1'),offer_active=False)
    policy=replace(POLICY,supplier_priority={'top':99,'partner_st':0})
    assert proposed_selection([disabled,inactive,active],policy)['selected']==[['partner_st','1']]

@pytest.mark.parametrize('cls',['REVIEW','CONFLICT'])
def test_unconfirmed_identity_no_selection(cls):
    row,_=offer_row(classification=cls)
    assert selection_view([row])==set()

def test_POR_null_and_FROM_PRICE_not_exact():
    row,_=offer_row(price=None,price_state='price_on_request')
    state=proposed_selection([row]);assert row['price'] is None
    # Generic policy may select one eligible request-price offer, but does not
    # invent a numeric price or treat it as a comparable priced offer.
    assert state['decisions'][0]['evidence']['candidate_offers'][0]['price'] is None
    lower,_=offer_row(price_basis='from_price')
    assert proposed_selection([lower])['overall_status']=='NO_ELIGIBLE_OFFER'

def test_same_fixture_proposed_and_generic_agree_deterministically():
    a,ca=offer_row('kami',7);b,cb=offer_row('partner_st',3)
    state=proposed_selection([b,a]);direct=evaluate_offers(7,[cb,ca],POLICY)
    assert state['decisions'][0]==asdict(direct)
    assert state==proposed_selection([a,b])
    assert persisted_selection_keys([a,b],state)=={('kami','7')}

def test_currency_and_quantity_review_not_invented():
    a,_=offer_row('kami',1);b,_=offer_row('partner_st',2,currency='USD')
    assert proposed_selection([a,b])['decisions'][0]['selection_status']=='review'
    q,_=offer_row(quantity=Decimal('0'))
    assert proposed_selection([q])['decisions'][0]['selection_status']=='review'

def test_no_cross_supplier_context_leakage():
    row,_=offer_row();row['selection_context']['candidate']['supplier_code']='optimum'
    with pytest.raises(ValueError,match='Cross-supplier'):proposed_selection([row])

def test_no_missing_activation_or_context_fallback():
    row,_=offer_row();row.pop('selection_context')
    assert proposed_selection([row])['overall_status']=='NO_ELIGIBLE_OFFER'

def test_export_reads_persisted_snapshot_and_never_reselects():
    from unittest.mock import patch
    row,_=offer_row();state=proposed_selection([row])
    with patch('universal_supplier.proposed_offer_selection.evaluate_offers',side_effect=AssertionError('hidden ranking')):
        assert persisted_selection_keys([row],state)=={('kami','1')}
        assert xml_bytes([row],[],{},selection_state=state)==xml_bytes([row],[],{},selection_state=state)
    bad=deepcopy(row);bad['offer_active']=False
    with pytest.raises(ValueError):persisted_selection_keys([bad],state)
