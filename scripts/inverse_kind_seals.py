"""Explicit archived kind29 -> inverse97 proposal; no implicit latest fallback."""
from scripts import run_ltt_uss_run1 as r
ROOT=r.BASE.parents[1]
B=r.BASE/'INVERSE_KIND_97_BATCH_2026-10-16'
ACCEPTED='PRODUCT_KIND29_ACCEPTED_CODE_SEAL'
PROPOSED='PROPOSED_INVERSE_KIND_97_CODE_SEAL'
PROVENANCE=B/'CHECKPOINT_PROVENANCE.json'
SHA='f2df9b92b82daf64968094a9d881c9c23e666bd0c68c11392eb58da5f30d171b'

def provenance():
    assert PROVENANCE.is_file(),'INVERSE_PROVENANCE_MISSING'
    assert r.digest(PROVENANCE.read_bytes())==SHA,'INVERSE_PROVENANCE_REWRITTEN'
    p=r.load(PROVENANCE)
    pin=p['old_accepted_seal'];assert r.digest((ROOT/pin['path']).read_bytes())==pin['sha256'],'HISTORICAL_SEAL_REWRITTEN'
    for label,files in p['archives'].items():
        for n,item in files.items():
            path=(ROOT/item['path']).resolve()
            archive_root=p['proposal_archive_root'] if label==PROPOSED else label
            assert path.is_relative_to((B/'ARCHIVED_CODE'/archive_root).resolve()) and path.is_file(),'INVERSE_ARCHIVED_CODE_MISSING'
            assert r.digest(path.read_bytes())==item['sha256'],'INVERSE_ARCHIVED_CODE_REWRITTEN'
    assert r.digest((B/'AUTHORIZED_97_ROWS.json').read_bytes())==p['authorized97_sha256'],'INVERSE_AUTHORIZATION_REWRITTEN'
    assert r.digest((B/'AUTHORIZED_TERMINAL4.json').read_bytes())==p['terminal4_sha256'],'INVERSE_AUTHORIZATION_REWRITTEN'
    return p

def named(name):
    if name not in {ACCEPTED,PROPOSED}:raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    p=provenance();assert name in p['archives'],'INVERSE_NAMED_SEAL_MISSING'
    return {n:item['sha256'] for n,item in p['archives'][name].items()}

def verify(*,baseline_seal,proposed_transition=None,working_hashes=None):
    assert baseline_seal==ACCEPTED,'EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED'
    before=named(ACCEPTED);expected=before
    if proposed_transition is not None:
        assert proposed_transition==PROPOSED,'UNAPPROVED_SEAL_TRANSITION'
        expected=named(PROPOSED)
        assert set(before)==set(expected)
        assert sorted(n for n in before if before[n]!=expected[n])==provenance()['approved_changed_paths'],'UNAPPROVED_BUSINESS_CODE_DELTA'
    actual={n:r.digest((ROOT/n).read_bytes()) for n in expected} if working_hashes is None else working_hashes
    assert actual==expected,'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
    return dict(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_code_hashes=actual,checkpoint_provenance_sha256=SHA,historical_origin_sha256=provenance()['old_accepted_seal']['sha256'])
