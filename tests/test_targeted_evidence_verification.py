from copy import deepcopy
from universal_supplier.targeted_evidence_verification import smec_table,labelled_power_pair,preserve_target_hold
from universal_supplier.offline_review_resolution import learn_cnc_mappings,compare_typed,resolve_existing,cnc_features
from tests.test_offline_review_resolution import pair

def test_pair_is_role_labelled_not_scalar_or_s6():
    p=labelled_power_pair('18,5/26','Cont./Max')
    assert p['continuous_kw']=='18.5' and p['maximum_kw']=='26'
    assert not p['scalar'] and not p['s6_equivalent']
    assert not labelled_power_pair('18.5/26','S6')['usable']
    assert not cnc_features([{'name':'Мощность шпинделя S6, (кВт)','value':'26'}],'lathe')
    assert not cnc_features([{'name':'Ход по оси X rapid (мм)','value':'245'}],'lathe')

def test_exact_manufacturer_table_no_sibling_or_drawtube_substitution():
    body=b'<table><th class="model_name">SL 3000L B</th><tr><td>Draw tube ID mm</td><td>91</td></tr><tr><td>Spindle bore diameter mm</td><td>105</td></tr></table><table><th class="model_name">SL 3000L A</th><tr><td>Spindle bore diameter mm</td><td>86</td></tr></table>'
    t=smec_table(body,'SL 3000L B')
    assert [r['values'][0] for r in t['rows']]==['91','105']
    assert preserve_target_hold({'confirmed':False},'105',False)['external_value_copied_to_both_sides'] is False

def test_x_geometry_requires_confirmed_only_scoped_training():
    props=[{'name':'Макс. длина обработки (мм)','value':'1028'}, {'name':'Ход по оси X, (мм)','value':'245'}]
    can=[{'name':'Макс. длина обработки, мм','value':'1028'}, {'name':'Ход по оси X, мм','value':'245'}]
    records=[{'source':'intervesp','external_id':str(i),'sterbrust_product_id':str(100+i),
              'classification':'EXISTING_CONFIRMED','full_model_confirmed':True,'equipment_type':'lathe',
              'source_properties':props,'sterbrust_properties':can} for i in range(3)]
    m=learn_cnc_mappings(records);assert len(m)==2 and all(p['verdict']=='SAFE' for p in m)
    r,t=pair('SL3000BL');r['observed_properties']=props;t['observed_properties']=can
    assert resolve_existing(r,t,m)['confirmed']
    assert not resolve_existing(r,t,learn_cnc_mappings([{**p,'classification':'REVIEW'} for p in records]))['confirmed']
    assert not resolve_existing({**r,'source':'other'},t,m)['confirmed']
    bad=deepcopy(t);bad['observed_properties'][1]['value']='250'
    assert 'TYPED_CHARACTERISTIC_CONTRADICTION' in resolve_existing(r,bad,m)['blocking_reasons']

def test_x_is_not_y_or_rapid_traverse_and_bore_is_not_inherited():
    props=[{'name':'Макс. длина обработки (мм)','value':'1028'}, {'name':'Ход по оси Y, (мм)','value':'245'}]
    p={'source':'intervesp','external_id':'1','sterbrust_product_id':'100','classification':'EXISTING_CONFIRMED',
       'full_model_confirmed':True,'equipment_type':'lathe','source_properties':props,'sterbrust_properties':props}
    mappings=learn_cnc_mappings([{**p,'external_id':str(i)} for i in range(3)])
    assert not any(m['key']=='axis_x_travel' for m in mappings)
    r,t=pair();r['observed_properties']=props;t['observed_properties']=props
    assert not compare_typed(r,t,mappings)['independent_identity_support']

def test_canonical_duplicate_cannot_choose_a_survivor_with_manufacturer_bore():
    r,t=pair();t['equivalent_identity_ids']=['123','124']
    proof=resolve_existing(r,t)
    assert not preserve_target_hold(proof,'105',True)['confirmed']
