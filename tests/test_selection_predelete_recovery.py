import pytest
from scripts.recover_kami_selection_predelete import exact_missing


def row(key,offer=10):
    return {'catalog_product_id':key,'selected_offer_id':offer,'selection_status':'selected',
            'rule_version':'original-v1','evidence':{'raw':'unchanged'},'evaluated_at':'2026-09-01T12:00:00+03:00'}


def test_exact_original_tuple_preserved():
    previous=[row(1),row(2)]
    assert exact_missing(previous,[row(1)],{2})==[row(2)]


def test_existing_conflicting_content_fails():
    with pytest.raises(ValueError):exact_missing([row(1),row(2)],[row(1,999)],{2})


def test_outside_scope_missing_fails():
    with pytest.raises(ValueError):exact_missing([row(1),row(2)],[],{2})


def test_partial_state_fails():
    with pytest.raises(ValueError):exact_missing([row(1),row(2)], [row(1)],{1,2})


def test_duplicate_or_extra_row_fails():
    with pytest.raises(ValueError):exact_missing([row(1),row(1)],[],{1})
    with pytest.raises(ValueError):exact_missing([row(1)],[row(1),row(2)],{1})


def test_second_recovery_exact_noop():
    previous=[row(1),row(2)]
    assert exact_missing(previous,previous,{2})==[]
