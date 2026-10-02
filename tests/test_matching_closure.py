from copy import deepcopy
import pytest
from universal_supplier.matching_closure import close_saved_rows, closure_record


def row(**kw):
    return dict(source='optimum', external_id='1', classification='REVIEW', **kw)


def test_discovery_complete_cannot_grant_canonical_identity():
    r=row(provisional=False, proposed_sterbrust_id='123', readiness={'identity_blockers':['NEW_NEEDS_SECTION']})
    old=deepcopy(r); result=closure_record(r)
    assert r==old and result['classification']=='REVIEW'
    assert result['candidate_ids_retrieval_only']==['123']
    assert 'sterbrust_product_id' not in result and not result['decision_changed']


def test_existing_requires_real_id_and_full_model():
    for pid, full in [('NEWC-123',True), ('123',False)]:
        with pytest.raises(ValueError):
            closure_record({'source':'optimum','external_id':'1','classification':'EXISTING_CONFIRMED',
                            'sterbrust_product_id':pid,'full_model_confirmed':full})


def test_conflict_and_source_quarantine_are_not_cleared():
    r=row(detail_review_reason='visible execution disagreement')
    assert closure_record(r)['next_evidence_route']=='saved_source_identity_quarantine'
    r['classification']='CONFLICT'
    assert closure_record(r)['terminal_route']=='CONFLICT_RETAINED'


def test_duplicates_rejected_and_repeat_is_pure():
    with pytest.raises(ValueError):close_saved_rows([row(),row()])
    assert close_saved_rows([row()])==close_saved_rows([row()])
