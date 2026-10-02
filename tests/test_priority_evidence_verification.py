from copy import deepcopy
import pytest
from universal_supplier.characteristic_evidence import property_key
from universal_supplier.priority_evidence_verification import visible_page,merge_observations,verify_candidate,apply_verified
from universal_supplier.offline_review_resolution import compare_typed
from tests.test_offline_review_resolution import pair


def test_genitive_bore_and_counterspindle_are_distinct():
    assert property_key('Диаметр отверстия шпинделя, мм','lathe')=='spindle_bore'
    for label in ('Отверстие противошпинделя, (мм)','Отверстие контршпинделя, мм','counter spindle bore','sub spindle bore','Конус шпинделя'):
        assert property_key(label,'lathe')!='spindle_bore'
    assert property_key('Диаметр отверстия шпинделя, мм','grinder') is None


def test_visible_parser_ignores_hidden_and_neighbours():
    p=visible_page(b'<h1>OWN</h1><link rel="canonical" href="https://a"><script>hidden</script><div id="elTabProp"><div class="elTabPropName">Bore</div><div class="elTabPropNum">52</div></div><div class="elTabPropName">Related</div><div class="elTabPropNum">100</div>')
    assert p['h1']=='OWN' and len(p['properties'])==1
    assert not visible_page(b'<h1>A</h1><h1>B</h1>')['h1']


def test_merge_preserves_same_label_contradictions():
    assert len(merge_observations([{'name':'Bore','value':'52'}],[{'name':'Bore','value':'66'}]))==2


def test_strict_saved_proof_and_previous_contradiction():
    r,t=pair();r['source_url']='https://a/'
    p={'h1':r['name'],'canonical_url':r['source_url'],'properties':r['observed_properties']}
    assert verify_candidate(r,t,p,[], 'saved://proof')['confirmed']
    assert not verify_candidate(r,t,p,[],'saved://proof',[{'key':'spindle_bore','reason':'canonical_values_disagree'}])['confirmed']
    p['canonical_url']='https://wrong/'
    assert not verify_candidate(r,t,p,[],'saved://proof')['confirmed']


@pytest.mark.parametrize('left,right',[('TU2304V','TU2304'),('LX20 Pro','LX20 NEW'),('HCV125 jaws','HCV125')])
def test_false_match_classes_stay_blocked(left,right):
    r,t=pair(left);r['source_url']='https://a/'
    t.update(model=right,name='Токарный станок Optimum '+right)
    p={'h1':r['name'],'canonical_url':r['source_url'],'properties':r['observed_properties']}
    assert not verify_candidate(r,t,p,[],'saved://proof')['confirmed']


def test_review_only_promotion_replay_and_immutable_identity():
    r,t=pair();before=deepcopy(r)
    proof={'confirmed':True};p={'source':r['source'],'external_id':'a','canonical_id':'123','canonical_name':t['name'],'proof':proof,'evidence_ref':'saved://proof'}
    out=apply_verified([r],[p]);assert r==before and out[0]['model']==r['model']
    assert apply_verified(out,[p])==out
    with pytest.raises(AssertionError):apply_verified([{**r,'classification':'CONFLICT'}],[p])
    with pytest.raises(AssertionError):apply_verified([r],[p,p])


def test_axis_option_and_duplicate_active_canonical_block():
    r,t=pair();r['source_url']='https://a/'
    t['properties']={'Наличие оси Y':'Нет'};t['observed_properties']+= [{'name':'Наличие оси Y','value':'Нет'}]
    p={'h1':r['name'],'canonical_url':r['source_url'],'properties':r['observed_properties']+[{'name':'Наличие оси Y','value':'Да'}]}
    assert not verify_candidate(r,t,p,[],'saved://proof')['confirmed']
    t['equivalent_identity_ids']=['123','124']
    assert not verify_candidate(r,t,p,[],'saved://proof')['confirmed']


def test_main_counterspindle_not_false_contradiction_but_main_difference_blocks():
    r,t=pair('S300SM/1100')
    p=[{'name':'Расстояние между центрами, мм','value':'1100'},
       {'name':'Отверстие в шпинделе, мм','value':'91'},
       {'name':'Отверстие противошпинделя, мм','value':'66'}]
    r['observed_properties']=deepcopy(p);t['observed_properties']=deepcopy(p)
    typed=compare_typed(r,t)
    assert typed['independent_identity_support'] and not typed['contradictions']
    t['observed_properties'][1]['value']='92'
    assert compare_typed(r,t)['contradictions']


def test_full_execution_length_guard():
    r,t=pair('CK7530/1500');r['source_url']='https://a/'
    p=[{'name':'Макс. длина обработки (мм)','value':'2000'}, {'name':'Отверстие в шпинделе, мм','value':'52'}]
    r['observed_properties']=p;t['observed_properties']=p
    mappings=[{'verdict':'SAFE','source':'intervesp','scope':'lathe','source_property':p[0]['name'],
               'canonical_property':p[0]['name'],'key':'max_processing_length'}]
    page={'h1':r['name'],'canonical_url':r['source_url'],'properties':p}
    assert 'FULL_EXECUTION_PROCESSING_LENGTH_CONTRADICTION' in verify_candidate(r,t,page,mappings,'saved://proof')['blocking_reasons']
