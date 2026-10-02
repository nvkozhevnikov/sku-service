from copy import deepcopy
from universal_supplier.new_readiness import propose_readiness
import pytest

ROW={'classification':'NEW_CANDIDATE','new_candidate_id':'NEW:test'}
ADVISORY={'CHARACTERISTIC_CONTRADICTION':{'blocked':False},
          'CANONICAL_ABSENCE_SUPPORT':{'verified':True}, 'SECTION_EVIDENCE':{'verified':True},
          'CHARACTERISTIC_IDENTITY_SUPPORT':{'identity_evidence_complete':True}}


def test_identity_ready_does_not_mean_full_or_sterbrust_id():
    result=propose_readiness(ROW,ADVISORY,dedup_verified=True,group_id='NEW_GROUP:test')
    assert result['state']=='READY_TO_CREATE_IDENTITY'
    assert not result['full_ready']
    assert result['sterbrust_product_id'] is None


@pytest.mark.parametrize('key', ['CANONICAL_ABSENCE_SUPPORT','SECTION_EVIDENCE','CHARACTERISTIC_IDENTITY_SUPPORT'])
def test_missing_identity_gate_cannot_be_bypassed_by_full_content(key):
    proof=deepcopy(ADVISORY); proof[key]={}
    assert not propose_readiness(ROW,proof,dedup_verified=True,group_id='NEW_GROUP:test')['identity_ready']


def test_review_and_conflict_never_promoted():
    assert propose_readiness({**ROW,'classification':'REVIEW'},ADVISORY,dedup_verified=True,group_id='NEW_GROUP:test')['state']=='REVIEW'
    assert propose_readiness({'classification':'CONFLICT'},ADVISORY,dedup_verified=True,group_id='NEW_GROUP:test')['state']=='CONFLICT'


def test_dedup_requires_independent_proof_not_group_id_alone():
    assert not propose_readiness(ROW,ADVISORY,dedup_verified=False,group_id='NEW_GROUP:test')['identity_ready']


def test_full_readiness_requires_actual_scoped_enrichment_provenance():
    full={'description_text':'Observed description','technical_properties':[('Diameter','100 mm')],
          'source_images':['https://intervesp.ru/photo.jpg'],'price_state':'price_on_request',
          'availability':'unknown','evidence_ref':'capture://verified',
          'enrichment_evidence':{'source_url':'https://intervesp.ru/catalog/a/b/',
              **{key+'_checked':True for key in ('description','characteristics','images','documents')}}}
    result=propose_readiness(ROW,ADVISORY,dedup_verified=True,group_id='NEW_GROUP:test',enrichment=full)
    assert result['state']=='READY_TO_CREATE_FULL'
    assert 'sterbrust_product_id' not in ROW
