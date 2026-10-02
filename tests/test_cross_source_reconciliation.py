from copy import deepcopy
from scripts.reconcile_cross_source_offline import evidence_view,empty_hold,url_numeric_model_contradiction,reconsiderable_automatic_hold
from universal_supplier.offline_review_resolution import compare_typed

def test_peer_model_is_not_context_evidence():
    r={'source':'beka_mak_tr','name':'BMSY-810CGH','source_category':'Cgh','properties':{},
       'enrichment':{},'product_kind':'unknown'}
    assert evidence_view(r)['product_kind']=='unknown'

def test_own_description_translation_preserves_original_and_hold():
    r={'source':'beka_mak_tr','name':'BMSY-810CGH','source_category':'Cgh','properties':{},
       'prior_review_retained':True,'enrichment':{'description_text':'Horizantal Bandsaws with linear guideways'},
       'observed_properties':[{'name':'Main Drive Motor','value':'4 kW'},{'name':'Band Dimensions','value':'8400x41x1,3 mm'}]}
    original=deepcopy(r); v=evidence_view(r)
    assert r==original and v['prior_review_retained'] is True
    assert v['observed_properties'][0]['raw_name']=='Main Drive Motor'
    t={'observed_properties':[{'name':'Мощность двигателя','value':'4 kW'},{'name':'Размеры ленточного полотна','value':'8400x41x1.3 mm'}]}
    assert compare_typed(v,t)['independent_identity_support']
    t['observed_properties'][0]['value']='5 kW'
    assert compare_typed(v,t)['contradictions']

def test_real_prior_conflict_is_not_empty_wrapper():
    assert empty_hold({'prior_source_review_not_auto_cleared':True,'prior_conflicts':'{}'})
    assert not empty_hold({'prior_conflicts':{'model':'different execution'}})

def test_new_evidence_can_recheck_retrieval_uncertainty_not_real_conflicts():
    assert reconsiderable_automatic_hold('{"semantic_identity":["name_compatibility_below_brand_model_threshold"]}')
    assert not reconsiderable_automatic_hold({'semantic_identity':['accessory_machine_contradiction']})
    assert not reconsiderable_automatic_hold({'model':{'source':'TU2304V','candidate':'TU2304'}})

def test_url_numeric_identity_contradiction_is_generic():
    assert url_numeric_model_contradiction('https://beka-mak.su/product/stanok_bmsy_320dgh/','BMSY 325 DGH')
    assert url_numeric_model_contradiction('https://beka-mak.su/product/stanok_bmso_200cs/','BMSO 201 CS')
    assert not url_numeric_model_contradiction('https://beka-mak.su/product/stanok_bmso_570xs/','BMSO 570 XS NC')

def test_additional_exact_blade_label_translation():
    r={'source':'beka_mak_tr','name':'BMSO-825XS','source_category':'XS','properties':{},
       'enrichment':{'description_text':'The ultimate bandsaw machine'},
       'observed_properties':[{'name':'Blade Dimensions','value':'9500x67x1,6 mm'}]}
    assert evidence_view(r)['observed_properties'][0]['name']=='Размеры ленточного полотна'
