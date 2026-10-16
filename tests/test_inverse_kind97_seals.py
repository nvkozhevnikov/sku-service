import copy
import pytest
from scripts import inverse_kind_seals as s

def test_actual_proposal_and_archived_accepted_are_distinct():
    before=s.named(s.ACCEPTED);after=s.named(s.PROPOSED)
    assert len(before)==len(after)==12
    assert {n for n in before if before[n]!=after[n]}=={'sterbrust_matching/product_identity.py','universal_supplier/adapters/ltt_uss_html.py'}
    # This transition is historical: verify its explicitly pinned archive bytes.
    assert s.verify(baseline_seal=s.ACCEPTED,proposed_transition=s.PROPOSED,working_hashes=s.named(s.PROPOSED))['working_code_hashes']==after
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):s.verify(baseline_seal=s.ACCEPTED)

def test_bad_working_hash_and_unapproved_transition_rejected():
    bad=copy.deepcopy(s.named(s.PROPOSED));bad[next(iter(bad))]='0'*64
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):s.verify(baseline_seal=s.ACCEPTED,proposed_transition=s.PROPOSED,working_hashes=bad)
    with pytest.raises(AssertionError,match='UNAPPROVED'):s.verify(baseline_seal=s.ACCEPTED,proposed_transition='LATEST')

@pytest.mark.parametrize('name',[None,'','LATEST','unknown'])
def test_no_implicit_seal(name):
    with pytest.raises(ValueError,match='EXPLICIT_APPROVED'):s.named(name)

def test_missing_and_changed_provenance_rejected(tmp_path,monkeypatch):
    p=tmp_path/'missing.json';monkeypatch.setattr(s,'PROVENANCE',p)
    with pytest.raises(AssertionError,match='PROVENANCE_MISSING'):s.named(s.ACCEPTED)
    p.write_text('{}',encoding='utf8')
    with pytest.raises(AssertionError,match='PROVENANCE_REWRITTEN'):s.named(s.ACCEPTED)

def test_archive_bytes_are_required_without_working_fallback(monkeypatch):
    real=s.r.load;p=copy.deepcopy(real(s.PROVENANCE));key=next(iter(p['archives'][s.ACCEPTED]));p['archives'][s.ACCEPTED][key]['sha256']='0'*64
    monkeypatch.setattr(s.r,'load',lambda path:p if path==s.PROVENANCE else real(path))
    with pytest.raises(AssertionError,match='ARCHIVED_CODE_REWRITTEN'):s.named(s.ACCEPTED)
