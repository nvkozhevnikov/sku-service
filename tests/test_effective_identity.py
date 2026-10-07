"""Shared effective identity acceptance. No DB/HTTP/export publication."""
from dataclasses import replace
import pytest
from test_stage5 import row,snapshot,POLICY
from universal_supplier.effective_identity import effective_identity_accepted
from universal_supplier.yml_feed import selected_row_violations,build_yml,FeedGenerationError
from universal_supplier.xml_export import generate_canonical_xml
from universal_supplier.release_export import validate
from sterbrust_matching.matching import Product,match_product


def proof(r):
    return {k:getattr(r,k) for k in ('source_product_id','source_catalog_product_id','catalog_product_id',
        'current_match_status','current_match_catalog_product_id','effective_manual_source_product_id','effective_manual_catalog_product_id')}


@pytest.mark.parametrize('status',['REVIEW','CONFLICT','NEW_CANDIDATE'])
def test_exact_manual_authority_yml_xml_agree(status):
    base=row();r=replace(base,current_match_status=status,current_match_catalog_product_id=base.catalog_product_id+1,
        effective_manual_source_product_id=base.source_product_id,effective_manual_catalog_product_id=base.catalog_product_id)
    assert effective_identity_accepted(**proof(r))
    assert build_yml(snapshot(r),POLICY).validation.valid
    assert generate_canonical_xml(snapshot(r),POLICY).manifest['exported_offer_count']==1
    validate([{'source':'partner_st','external_id':'fixture','classification':'EXISTING_CONFIRMED',
               'sterbrust_product_id':r.sterbrust_product_id,'effective_identity_evidence':proof(r)}],[])


@pytest.mark.parametrize('changes',[
    {'effective_manual_catalog_product_id':-1},
    {'effective_manual_source_product_id':-1},
    {'source_catalog_product_id':-1},
    {'source_catalog_product_id':None},
    {'source_product_id':None},
    {'catalog_product_id':0},
    {'source_product_id':0},
])
def test_exact_relation_required_not_any_manual(changes):
    base=row();r=replace(base,current_match_status='REVIEW',effective_manual_source_product_id=base.source_product_id,
        effective_manual_catalog_product_id=base.catalog_product_id,**{k:v for k,v in changes.items() if k not in {'effective_manual_source_product_id','effective_manual_catalog_product_id'}})
    r=replace(r,**changes)
    assert not effective_identity_accepted(**proof(r))
    with pytest.raises(FeedGenerationError):build_yml(snapshot(r),POLICY)
    assert generate_canonical_xml(snapshot(r),POLICY).manifest['exported_offer_count']==0
    with pytest.raises(ValueError,match='effective identity'):
        validate([{'source':'partner_st','external_id':'fixture','classification':'EXISTING_CONFIRMED',
                   'sterbrust_product_id':r.sterbrust_product_id,'effective_identity_evidence':proof(r)}],[])


@pytest.mark.parametrize('changes',[
    {'supplier_enabled':False}, {'offer_active':False}, {'source_active':False},
    {'sterbrust_active':False}, {'selected_offer_id':None}, {'price_type':'unknown'},
    {'availability_normalized':'discontinued'},
])
def test_manual_identity_does_not_bypass_commercial_guards(changes):
    base=row();r=replace(base,current_match_status='REVIEW',effective_manual_source_product_id=base.source_product_id,
        effective_manual_catalog_product_id=base.catalog_product_id,**changes)
    assert effective_identity_accepted(**proof(r))
    assert selected_row_violations((r,),POLICY,1)
    with pytest.raises(FeedGenerationError):build_yml(snapshot(r),POLICY)
    assert generate_canonical_xml(snapshot(r),POLICY).manifest['exported_offer_count']==0


@pytest.mark.parametrize('model',[
    'LX20 Pro vs LX20 NEW','PP-13D NEW vs base','ETM-16U controller','ETM-16U handle',
    'HCV125 jaws vs vise','TU2304V vs TU2304','KAMI accessory',
])
@pytest.mark.parametrize('status',['REVIEW','CONFLICT'])
def test_false_match_has_no_implicit_manual_authority(model,status):
    r=replace(row(),current_match_status=status,sterbrust_name=model)
    assert not effective_identity_accepted(**proof(r))
    with pytest.raises(FeedGenerationError):build_yml(snapshot(r),POLICY)
    assert generate_canonical_xml(snapshot(r),POLICY).manifest['exported_offer_count']==0


@pytest.mark.parametrize('brand,name,model,target_name,target_model',[
    ('JET','Станок LX20 Pro','LX20 Pro','Станок LX20 NEW','LX20 NEW'),
    ('Partner','Заточной станок PP-13D NEW','PP-13D NEW','Заточной станок PP-13D','PP-13D'),
    ('Partner','Контроллер для манипулятора ETM-16U','ETM-16U','Электрический манипулятор ETM-16U','ETM-16U'),
    ('Partner','Рукоятка для манипулятора ETM-16U','ETM-16U','Электрический манипулятор ETM-16U','ETM-16U'),
    ('OPTIMUM','Губки для тисков HCV125','HCV125','Тиски HCV125','HCV125'),
    ('OPTIMUM','Токарный станок TU2304V','TU2304V','Токарный станок TU2304','TU2304'),
    ('KAMI','Контроллер для станка M200','M200','Токарный станок M200','M200'),
])
def test_real_matcher_false_match_guards_unchanged(brand,name,model,target_name,target_model):
    source=Product(key='source',name=name,model=model,brand=brand)
    target=Product(key='canonical',name=target_name,model=target_model,brand=brand)
    assert not match_product(source,[target]).auto_accepted
