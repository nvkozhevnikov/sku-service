from copy import deepcopy
import json
from pathlib import Path
import pytest
from universal_supplier.beka_canonical_offline import (
    canonical_view, SavedCandidateIndex, evaluate_row, apply_proposals,
    saved_description_properties,
)


def fixture(model='BMSO-440CDGS', pid='146980'):
    props=[{'name':'Размеры ленточного полотна, мм','value':'6000 x 41 x 1,3'},
           {'name':'Главный привод, кВт','value':'4'}, {'name':'Тип конструкции','value':'Двухколонный'}]
    row={'source':'beka_mak','external_id':'19171','model':model,'execution':model,'brand':'Beka-Mak',
         'name':'Двухколонный ленточнопильный станок Beka-Mak '+model,
         'source_category':'Ленточнопильные станки','model_role':'OWN_MODEL',
         'classification':'REVIEW','match_status':'HIGH_CONFIDENCE_MATCH','observed_properties':props,
         'properties':{p['name']:p['value'] for p in props}, 'evidence_ref':'saved://capture'}
    record={'sterbrust_product_id':pid,'model_raw':model,'brand_raw':'Beka-Mak',
            'name':row['name'],'active':'Y','category_path':row['source_category'],'properties':[],
            'raw_data':{'detailText':'<table><tr><td>Размеры полотна, (мм)</td><td>6000*41*1,3</td></tr>'
                '<tr><td>Мощность двигателя, (кВт)</td><td>4</td></tr>'
                '<tr><td>Тип станка</td><td>Двухколонный</td></tr></table>'}}
    return row,record


def test_saved_description_closes_support_without_source_mutation():
    row,record=fixture(); original=deepcopy((row,record)); target=canonical_view(record)
    index=SavedCandidateIndex({'146980':target})
    after,audit,changes=apply_proposals([row],index)
    assert after[0]['classification']=='EXISTING_CONFIRMED'
    assert after[0]['sterbrust_product_id']=='146980'
    assert (row,record)==original
    assert after[0]['model']==row['model'] and after[0]['observed_properties']==row['observed_properties']
    assert apply_proposals(after,index)[0]==after and not apply_proposals(after,index)[2]


def test_structured_description_contradiction_never_overwritten():
    row,record=fixture()
    record['properties']=[{'property_name':'Мощность, Вт','value_flat':'5200','unit':'Вт'}]
    result=evaluate_row(row,SavedCandidateIndex({'146980':canonical_view(record)}))
    assert not result['confirmed']
    assert 'TYPED_CHARACTERISTIC_CONTRADICTION' in result['blocking_reasons']


def test_description_links_and_nested_tables_not_identity_evidence():
    _,record=fixture()
    record['raw_data']['detailText']='<table><tr><td>Размеры полотна, мм</td><td><a href="/other">6000x41x1.3</a></td></tr></table>'
    assert not saved_description_properties(record)


@pytest.mark.parametrize('left,right', [('TU2304V','TU2304'),('LX20 Pro','LX20 NEW'),
    ('BMSO-320GS NC','BMSO325C'),('BMSO-320GS NC','BMSO320GS')])
def test_retrieval_never_accepts_execution_or_numeric_neighbour(left,right):
    row,record=fixture(left); record['model_raw']=right; record['name']='Ленточнопильный станок Beka-Mak '+right
    index=SavedCandidateIndex({'146980':canonical_view(record)})
    result=evaluate_row(row,index)
    assert not result['confirmed'] and not result['retrieval']['exact_full_model_ids']


def test_accessory_machine_context_never_accepted():
    row,record=fixture('HCV125'); row['name']='Губки HCV125 для тисков'; record['name']='Тиски HCV125'
    assert not evaluate_row(row,SavedCandidateIndex({'146980':canonical_view(record)}))['confirmed']


def test_duplicate_full_model_and_inactive_target_fail_closed():
    row,record=fixture(); second=deepcopy(record); second['sterbrust_product_id']='2'
    result=evaluate_row(row,SavedCandidateIndex({'146980':canonical_view(record),'2':canonical_view(second)}))
    assert not result['confirmed'] and 'CANONICAL_IDENTITY_NOT_UNIQUE' in result['blocking_reasons']
    record['active']='N'
    assert not evaluate_row(row,SavedCandidateIndex({'146980':canonical_view(record)}))['confirmed']


def test_holds_and_other_namespace_and_existing_immutable():
    row,record=fixture(); row['prior_review_retained']=True
    index=SavedCandidateIndex({'146980':canonical_view(record)})
    assert apply_proposals([row],index)[0]==[row]
    row['source']='intervesp'; assert apply_proposals([row],index)[0]==[row]
    row['source']='beka_mak'; row['classification']='EXISTING_CONFIRMED'
    assert apply_proposals([row],index)[0]==[row]


def test_alias_does_not_enter_acceptance_index():
    row,record=fixture('BMSO320GS NC'); record['model_raw']='BMSO320GS'
    record['name']='Ленточнопильный станок Beka-Mak BMSO320GS'
    record['aliases']=['BMSO320GS NC']
    assert not evaluate_row(row,SavedCandidateIndex({'146980':canonical_view(record)}))['confirmed']


def test_current_checkpoint_real_priority_regressions():
    root=Path(__file__).resolve().parents[1]
    base=root/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED'
    audit=json.loads((base/'CANONICAL_AUDIT.json').read_text(encoding='utf-8'))
    byid={r['external_id']:r for r in audit if r['source']=='beka_mak'}
    assert byid['19154']['after']=='REVIEW' and not byid['19154']['confirmed']
    assert '147021' in byid['19154']['retrieval']['family_retrieval_only_ids']
    assert '17170' not in byid['19154']['retrieval']['exact_full_model_ids']
    assert byid['19166']['after']=='REVIEW'
    assert set(byid['19166']['retrieval']['exact_full_model_ids'])=={'17174','97207'}
    assert all(not c['confirmed'] for c in byid['19166']['candidates'])
    assert byid['19171']['confirmed']['sterbrust_product_id']=='146980'
    verified=json.loads((base/'VERIFIED.json').read_text(encoding='utf-8'))
    assert verified['replay_exact_no_op'] and verified['all_source_fields_unchanged']
    assert verified['http_requests']==verified['database_operations']==0


def test_unrelated_model_only_conflict_is_review_not_identity_confirmation():
    row,record=fixture('BMSO-320GS NC','17170')
    record.update(model_raw='BMSO325C',name='Ленточнопильный станок Beka-Mak BMSO325C')
    row.update(classification='CONFLICT',proposed_sterbrust_id='17170',
               match_conflicts={'model':{'candidate':'bmso325c','source':'bmso320gsnc'}})
    index=SavedCandidateIndex({'17170':canonical_view(record)})
    after,_,_=apply_proposals([row],index)
    assert after[0]['classification']=='REVIEW' and not after[0].get('sterbrust_product_id')
    assert apply_proposals(after,index)[0]==after
    row['axis_execution_evidence']={'blocked':True}
    assert apply_proposals([row],index)[0]==[row]
