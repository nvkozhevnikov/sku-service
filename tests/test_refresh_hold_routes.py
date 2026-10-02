from copy import deepcopy
import pytest
from scripts import match_fresh_refresh_consolidated as subject


@pytest.mark.parametrize('nested', [
    ['prior_review_requires_manual_identity_confirmation'],
    ['source_url_not_in_pinned_full_manifest'],
    {'semantic_identity': ['name_compatibility_below_brand_model_threshold']},
    {'model': ['old retrieved candidate differs']},
])
def test_historical_flag_is_not_a_factual_contradiction(monkeypatch, nested):
    original = {'match_conflicts': {'prior_source_review_not_auto_cleared': True,
                                  'prior_conflicts': nested}, 'prior_review_retained': True}
    before = deepcopy(original)
    def evaluate(view, index):
        assert view['match_conflicts'] == {} and not view['prior_review_retained']
        return {'confirmed': None, 'blocking_reasons': ['INDEPENDENT_TYPED_IDENTITY_SUPPORT_MISSING']}
    monkeypatch.setattr(subject, 'evaluate_row', evaluate)
    result = subject.reassess_historical_hold(original, None)
    assert original == before and result['confirmed'] is None
    assert result['hold_verdict'] == 'CANONICAL_SIDE_EVIDENCE_REQUIRED_NOT_HUMAN_BY_DEFAULT'


def test_execution_blocker_cannot_be_cleared_by_removing_workflow_hold(monkeypatch):
    nested = {'semantic_identity': ['execution_or_version_mismatch']}
    original = {'match_conflicts': {'prior_source_review_not_auto_cleared': True,
                                  'prior_conflicts': nested}, 'prior_review_retained': True}
    monkeypatch.setattr(subject, 'evaluate_row', lambda view, index: {
        'confirmed': {'sterbrust_product_id': 'fixture'}, 'blocking_reasons': []})
    result = subject.reassess_historical_hold(original, None)
    assert result['confirmed'] is None
    assert 'RETAINED_FACTUAL_CONFLICT' in result['blocking_reasons']
