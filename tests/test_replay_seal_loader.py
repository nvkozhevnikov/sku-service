"""Provenance-only gates. No parser or commercial assertion changes."""
import copy
import pytest
from scripts import ltt_uss_replay_seals as s
from scripts import run_ltt_uss_run2_offline as replay

def test_original_seal_verifiable_as_immutable_history():
    p=s.provenance()
    assert s.baseline_evidence(s.ORIGINAL)==replay.r.load(replay.r.OUT/'CODE_SEAL.json')
    assert p['original_seal']['sha256']=='20bb8e221f9e1c43f8b2f575914d55c019caeb9b0a8c698764fe330e704af1f3'

def test_accepted_pre_batch_evidence_is_exact_verified_checkpoint():
    p=s.provenance();accepted=s.baseline_evidence(s.CURRENT)
    assert p['accepted_pre_batch_seal']['sha256']=='ac2117eec4d17759afc84928fb259d5ffc498137b098cfdcc80e86676c1f8056'
    assert s.verify_active_code(baseline_seal=s.CURRENT,working_hashes=accepted)['baseline_seal']==s.CURRENT
    assert p['run2']==dict(scope=2634,exact_noop=2445,terminal_delta=0)

def test_proposed_code_is_allowed_only_with_exact_explicit_transition():
    # Historical terminal proposal verifies against archived terminal bytes;
    # current product-kind working code has a separate explicit transition.
    result=s.verify_active_code(baseline_seal=s.CURRENT,proposed_transition=s.PROPOSAL,
        working_hashes=s.named_seal(s.TERMINAL))
    assert result['working_code_hashes']==s.provenance()['proposed_batch_code_hashes']

@pytest.mark.parametrize('baseline',[s.CURRENT,s.ORIGINAL])
def test_wrong_working_code_refused(baseline):
    wrong=copy.deepcopy(s.baseline_evidence(baseline));wrong[next(iter(wrong))]='0'*64
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):
        s.verify_active_code(baseline_seal=baseline,working_hashes=wrong)

def test_original_execution_guard_rejects_current_proposal():
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):
        replay.frozen_run(baseline_seal=s.ORIGINAL)

def test_current_prebatch_without_explicit_proposal_refuses_working_proposal():
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):
        replay.frozen_run(baseline_seal=s.CURRENT)

@pytest.mark.parametrize('value',[None,'','LATEST','whatever-current','UNAPPROVED'])
def test_missing_or_unknown_seal_fails_closed(value):
    with pytest.raises(ValueError,match='EXPLICIT_APPROVED_BASELINE'):
        replay.frozen_run(baseline_seal=value)

@pytest.mark.parametrize('baseline,proposal',[(s.CURRENT,'UNKNOWN'),(s.ORIGINAL,s.PROPOSAL)])
def test_unapproved_transition_fails_closed(baseline,proposal):
    with pytest.raises(AssertionError,match='UNAPPROVED_SEAL_TRANSITION'):
        s.verify_active_code(baseline_seal=baseline,proposed_transition=proposal)

def test_missing_provenance_file_refused(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'PROVENANCE',tmp_path/'missing.json')
    with pytest.raises(AssertionError,match='PROVENANCE_MISSING'):s.baseline_evidence(s.CURRENT)

def test_rewritten_provenance_file_refused(tmp_path,monkeypatch):
    file=tmp_path/'provenance.json';file.write_text('{}',encoding='utf8')
    monkeypatch.setattr(s,'PROVENANCE',file)
    with pytest.raises(AssertionError,match='PROVENANCE_REWRITTEN'):s.baseline_evidence(s.CURRENT)

def test_explicit_chain_preserves_failed_and_accepted_transitions():
    chain=s.provenance()['chain']
    assert [n['stage'] for n in chain]==['ORIGINAL_RUN1','COMMERCIAL_TABLE_CORRECTION','H2_TITLE_LOCAL_FIX','H2_TITLE_WITH_SOURCE_URL_HANDOFF','CURRENT_ACCEPTED_PRE_BATCH_SEAL','TERMINAL_BATCH_FIX_PROPOSAL']
    assert chain[0]['accepted'] and chain[1]['accepted']
    assert not chain[2]['accepted'] and chain[3]['accepted']
    assert chain[3]['code_hashes']==chain[4]['code_hashes']
    assert not chain[5]['accepted']

def test_pinned_historical_artifact_mutation_refused(monkeypatch):
    real=s.r.load;prov=copy.deepcopy(real(s.PROVENANCE));prov['pins'][0]['sha256']='0'*64
    monkeypatch.setattr(s.r,'load',lambda path:prov if path==s.PROVENANCE else real(path))
    with pytest.raises(AssertionError,match='HISTORICAL_EVIDENCE_REWRITTEN'):s.provenance()
