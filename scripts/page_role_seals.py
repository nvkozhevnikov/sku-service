"""Explicit hotspot36 -> page-role gate transition. Historical seals stay immutable."""
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o

PHASE = r.BASE / 'PAGE_ROLE_SCOPE_2026-10-16'
PROVENANCE = PHASE / 'PAGE_ROLE_CODE_PROVENANCE.json'
ACCEPTED = 'DISK_ALIAS_10_CODE_SEAL'
PRIOR = 'FINAL_HOTSPOT_36_CODE_SEAL'
PROPOSED = 'PAGE_ROLE_GATE_CODE_SEAL'
SHA = 'e36b28d041f6e4bda213338cf7d5795f42fb4b6d3571c5acb9992fd72e386a1d'


def provenance():
    assert PROVENANCE.is_file(), 'PAGE_ROLE_PROVENANCE_MISSING'
    assert r.digest(PROVENANCE.read_bytes()) == SHA, 'PAGE_ROLE_PROVENANCE_REWRITTEN'
    p = r.load(PROVENANCE)
    from scripts.final_hotspot36_seals import PROPOSED as HOT_PROPOSED
    from scripts.final_hotspot36_seals import SHA as PRIOR_SHA
    from scripts.final_hotspot36_seals import named
    assert p['previous_provenance_sha256'] == PRIOR_SHA
    assert p['baseline'] == PRIOR and p['proposal'] == PROPOSED and p['chain_root'] == ACCEPTED
    assert p['before'] == named(HOT_PROPOSED)
    assert p['changed_business_paths'] == [
        'universal_supplier/http_capture.py',
        'universal_supplier/ltt_uss_capture.py',
    ]
    assert set(p['before']) == set(p['after'])
    assert sorted(name for name in p['before'] if p['before'][name] != p['after'][name]) == p['changed_business_paths']
    for label, folder in (('before', 'ARCHIVED_BEFORE_CODE'), ('after', 'ARCHIVED_AFTER_CODE')):
        for name, sha in p[label].items():
            assert r.digest((PHASE / folder / name).read_bytes()) == sha, ('PAGE_ROLE_ARCHIVE_DRIFT', label, name)
    return p


def named(name):
    if name not in {PRIOR, PROPOSED}:
        raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    p = provenance()
    return p['before' if name == PRIOR else 'after']


def verify(*, baseline_seal, proposed_transition=None, working_hashes=None):
    assert baseline_seal == ACCEPTED, 'EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED'
    assert proposed_transition == PROPOSED, 'UNAPPROVED_SEAL_TRANSITION'
    expected = provenance()['after']
    actual = {name: r.digest((o.ROOT / name).read_bytes()) for name in expected} if working_hashes is None else working_hashes
    assert actual == expected, 'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
    return dict(baseline_seal=baseline_seal, proposed_transition=proposed_transition,
                working_code_hashes=actual, checkpoint_provenance_sha256=SHA)
