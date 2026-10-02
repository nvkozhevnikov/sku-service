from copy import deepcopy
from universal_supplier.offline_review_resolution import clean_observed_model, resolve_existing, reason_clusters, learn_cnc_mappings


def pair(model='TU2304V'):
    props=[{'name':'Расстояние между центрами, мм','value':'450'}, {'name':'Отверстие в шпинделе, мм','value':'26'}]
    r={'source':'intervesp','external_id':'a','classification':'REVIEW','match_status':'HIGH_CONFIDENCE_MATCH',
       'brand':'Optimum','model':model,'execution':model,'name':'Токарный станок Optimum '+model,
       'source_category':'Токарные станки','model_role':'OWN_MODEL','observed_properties':props,'evidence_ref':'saved://a'}
    t={'sterbrust_product_id':'123','brand':'Optimum','model':model,'name':'Токарный станок Optimum '+model,
       'source_category':'Токарные станки','observed_properties':props,'active':'Y'}
    return r,t


def test_prefix_only_preserves_execution():
    row={'brand':'STS','model':'с ЧПУ STS CTX46 WP1','name':'Станок STS CTX46 WP1'}
    assert clean_observed_model(row)['model']=='CTX46 WP1'
    assert row['model']=='с ЧПУ STS CTX46 WP1'


def test_proven_pair_and_no_mutation():
    r,t=pair(); old=deepcopy((r,t))
    assert resolve_existing(r,t)['confirmed']
    assert (r,t)==old


def test_suffix_never_collapsed():
    r,t=pair(); t['model']='TU2304'; t['name']='Токарный станок Optimum TU2304'
    assert not resolve_existing(r,t)['confirmed']


def test_no_characteristics_no_confirmation():
    r,t=pair(); r['observed_properties']=[]
    assert not resolve_existing(r,t)['confirmed']


def test_critical_contradiction_and_prior_review():
    r,t=pair(); t['observed_properties']=[{'name':'Расстояние между центрами, мм','value':'500'}]
    assert 'TYPED_CHARACTERISTIC_CONTRADICTION' in resolve_existing(r,t)['blocking_reasons']
    r,t=pair(); r['prior_review_retained']=True
    assert not resolve_existing(r,t)['confirmed']


def test_known_false_cases():
    for left,right in [('LX20 Pro','LX20 NEW'),('HCV125 jaws','HCV125'),('TU2304V','TU2304')]:
        r,t=pair(left); t.update(model=right,name='Токарный станок Optimum '+right)
        assert not resolve_existing(r,t)['confirmed']


def test_reason_count_row_unique():
    assert reason_clusters([{'blocking_reasons':['A','A']},{'blocking_reasons':['A','B']}])[0]=={'reason':'A','blocked_rows':2}


def test_missing_brand_and_duplicate_canonical_fail_closed():
    r,t=pair(); r['brand']=''; t['brand']=''
    assert not resolve_existing(r,t)['confirmed']
    r,t=pair(); t['equivalent_identity_ids']=['123','456']
    assert 'CANONICAL_IDENTITY_NOT_UNIQUE' in resolve_existing(r,t)['blocking_reasons']


def test_canonical_brand_prefix_proven_by_own_name_not_execution():
    r,t=pair('HK63B/2000'); r.update(brand='BLIN MACHINERY',name='Токарный станок BLIN HK63B/2000')
    t.update(brand='BLIN MACHINERY',name='Токарный станок BLIN HK63B/2000',model='BLIN HK63B/2000')
    assert resolve_existing(r,t)['confirmed']
    t.update(name='Токарный станок BLIN HK63B/1500',model='BLIN HK63B/1500')
    assert not resolve_existing(r,t)['confirmed']


def test_cnc_synonyms_only_confirmed_scoped_and_unanimous():
    props=[{'name':'Макс. длина обработки (мм)','value':'500'}, {'name':'Мощность шпинделя (кВт)','value':'7,5'}]
    canonical=[{'name':'Макс. длина обработки, мм','value':'500'}, {'name':'Мощность шпинделя, кВт','value':'7.5'}]
    pairs=[{'source':'intervesp','external_id':str(i),'sterbrust_product_id':str(10+i),
            'classification':'EXISTING_CONFIRMED','full_model_confirmed':True,'equipment_type':'lathe',
            'source_properties':props,'sterbrust_properties':canonical} for i in range(3)]
    mappings=learn_cnc_mappings(pairs)
    assert len(mappings)==2 and all(m['verdict']=='SAFE' for m in mappings)
    r,t=pair(); r.update(source='intervesp',observed_properties=props);t['observed_properties']=canonical
    assert resolve_existing(r,t,mappings)['confirmed']
    assert not resolve_existing({**r,'source':'other'},t,mappings)['confirmed']
    assert not learn_cnc_mappings([{**p,'classification':'NEW_CANDIDATE'} for p in pairs])
    mixed=deepcopy(pairs);mixed[0]['sterbrust_properties'][0]['value']='600'
    assert next(m for m in learn_cnc_mappings(mixed) if m['key']=='max_processing_length')['verdict']=='REVIEW'


def test_cnc_typed_contradiction_blocks_even_exact_model():
    from universal_supplier.offline_review_resolution import compare_typed
    r,t=pair(); r['observed_properties']=[{'name':'Макс. длина обработки (мм)','value':'500'}]
    t['observed_properties']=[{'name':'Макс. длина обработки, мм','value':'600'}]
    mapping={'source':'intervesp','scope':'lathe','source_property':'Макс. длина обработки (мм)',
             'canonical_property':'Макс. длина обработки, мм','key':'max_processing_length','verdict':'SAFE'}
    assert compare_typed(r,t,[mapping])['contradictions']
