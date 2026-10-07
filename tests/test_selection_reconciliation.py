"""Class-level reconciliation regression; no supplier HTTP or live database."""
import copy
import pytest
from universal_supplier.selection_reconciliation import selection_diff, delta_counts, apply_diff
from universal_supplier.proposed_offer_selection import proposed_selection


def old(offer=10, canonical=1, status='selected'):
    return {'catalog_product_id':canonical,'selected_offer_id':offer,'selection_status':status}


def desired(offer=10):
    return {'selected_offer_id':offer,'rule_version':'offer-selection-v1','evidence':{}}


@pytest.mark.parametrize('blocker',['REVIEW','CONFLICT','DISABLED','INACTIVE','NO_ELIGIBLE'])
def test_blocked_current_winner_removed(blocker):
    # Eligibility belongs to shared policy; absence of a valid expected winner
    # always removes the scoped persisted winner, regardless of historical cause.
    assert selection_diff([old()],{}, {1})[0]['action']=='DELETE'


def test_valid_existing_unchanged():
    assert delta_counts(selection_diff([old()],{1:desired()}, {1}))=={'KEEP':1,'DELETE':0,'INSERT':0,'UPDATE':0}


def test_changed_winner_update():
    d=selection_diff([old()],{1:desired(11)}, {1})[0]
    assert (d['action'],d['persisted_offer_id'],d['expected_offer_id'])==('UPDATE',10,11)


def test_insert_new_confirmed_winner():
    assert selection_diff([],{1:desired()}, {1})[0]['action']=='INSERT'


def test_unrelated_and_nonwinner_preserved():
    diff=selection_diff([old(canonical=2),old(None,status='no_eligible_offer')],{}, {1})
    assert all(d['action']=='KEEP' for d in diff)


def test_duplicate_or_outside_scope_fail_closed():
    with pytest.raises(ValueError): selection_diff([old(),old()],{}, {1})
    with pytest.raises(ValueError): selection_diff([], {2:desired()}, {1})


def test_repeat_exact_noop_no_legacy_fallback():
    before=[old(10),old(11,2),old(12,3)]
    expected={1:desired()}
    diff=selection_diff(before,expected,{1,2,3})
    retained=[r for r in before if r['catalog_product_id'] not in {d['catalog_product_id'] for d in diff if d['action']=='DELETE'}]
    assert dict((r['catalog_product_id'],r['selected_offer_id']) for r in retained)=={1:10}
    assert delta_counts(selection_diff(retained,expected,{1,2,3}))=={'KEEP':1,'DELETE':0,'INSERT':0,'UPDATE':0}


class MemoryTransaction:
    def __init__(self,rows): self.rows=copy.deepcopy(rows)
    def __enter__(self): self.before=copy.deepcopy(self.rows);return self
    def __exit__(self,kind,value,trace):
        if kind: self.rows=self.before
    def execute(self,query,values):
        assert query.startswith('DELETE')
        canonical,offer=values
        count=sum(r['catalog_product_id']==canonical and r['selected_offer_id']==offer for r in self.rows)
        self.rows=[r for r in self.rows if not (r['catalog_product_id']==canonical and r['selected_offer_id']==offer)]
        return type('Cursor',(),{'rowcount':count})()


def test_failed_invariant_transaction_rolls_back():
    db=MemoryTransaction([old()]);before=copy.deepcopy(db.rows)
    with pytest.raises(ValueError):
        with db:
            apply_diff(db,selection_diff(db.rows,{}, {1}),{})
            raise ValueError('post-apply invariant mismatch')
    assert db.rows==before


def test_exact_delete_detects_concurrent_drift():
    with pytest.raises(ValueError): apply_diff(MemoryTransaction([]),selection_diff([old()],{}, {1}),{})


@pytest.mark.parametrize('classification',['REVIEW','CONFLICT'])
def test_policy_adapter_cannot_select_unconfirmed(classification):
    state=proposed_selection([{'source':'test','external_id':'x','classification':classification}])
    assert state['selected']==[]


def test_committed_receipt_matches_policy_and_requires_sql_hash():
    from tests.test_kami_advisory_selection_boundaries import offer_row
    from universal_supplier.proposed_offer_selection import persisted_selection_keys
    row,_=offer_row()
    state=proposed_selection([row])
    state.update(status='LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION',persisted_table_sha256='verified-table-hash')
    assert persisted_selection_keys([row],state)=={('kami','1')}
    state.pop('persisted_table_sha256')
    with pytest.raises(ValueError,match='fingerprint'): persisted_selection_keys([row],state)


def test_committed_receipt_cannot_select_review_or_legacy_unknown_offer():
    from tests.test_kami_advisory_selection_boundaries import offer_row
    from universal_supplier.proposed_offer_selection import persisted_selection_keys,input_hash
    row,_=offer_row(classification='REVIEW')
    state={'status':'LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION','input_sha256':input_hash([row]),
           'persisted_table_sha256':'verified-table-hash','selected':[['kami','1']]}
    with pytest.raises(ValueError): persisted_selection_keys([row],state)
    state['selected']=[['partner_st','legacy-117']]
    with pytest.raises(ValueError): persisted_selection_keys([row],state)
