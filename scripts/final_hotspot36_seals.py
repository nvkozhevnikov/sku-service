"""Explicit disk10 -> authorized final36 business-code transition."""
from scripts.final_hotspot36 import S,T,r,o,AUTH_SHA
ACCEPTED='DISK_ALIAS_10_CODE_SEAL'
PROPOSED='FINAL_HOTSPOT_36_CODE_SEAL'
SHA='7eececd645081c93b6236d23e542147432969c47afadd0e77e518470e224ae31'

def provenance():
    path=S/'CODE_PROVENANCE.json';assert r.digest(path.read_bytes())==SHA,'HOTSPOT_PROVENANCE_DRIFT';p=r.load(path)
    assert p['authorization_sha256']==AUTH_SHA and r.digest((S/'AUTHORIZED_36_ROWS.json').read_bytes())==AUTH_SHA
    assert r.digest((T/'DISK_CODE_PROVENANCE.json').read_bytes())==p['previous_provenance_sha256']
    for label,folder in [('before','ARCHIVED_ACCEPTED_CODE'),('after','ARCHIVED_PROPOSED_CODE')]:
      for n,sha in p[label].items():assert r.digest((S/folder/n).read_bytes())==sha,('ARCHIVE_DRIFT',n)
    return p

def named(name):
    if name not in {ACCEPTED,PROPOSED}:raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    return provenance()['before' if name==ACCEPTED else 'after']

def verify(*,baseline_seal,proposed_transition=None,working_hashes=None):
    if proposed_transition=='PAGE_ROLE_GATE_CODE_SEAL':
        from scripts.page_role_seals import verify as verify_page_role
        return verify_page_role(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_hashes=working_hashes)
    assert baseline_seal==ACCEPTED,'EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED'
    expected=named(ACCEPTED)
    if proposed_transition is not None:
      assert proposed_transition==PROPOSED,'UNAPPROVED_SEAL_TRANSITION';before=expected;expected=named(PROPOSED)
      assert set(before)==set(expected) and sorted(n for n in before if before[n]!=expected[n])==provenance()['changed_business_paths']
    actual={n:r.digest((o.ROOT/n).read_bytes()) for n in expected} if working_hashes is None else working_hashes
    assert actual==expected,'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
    return dict(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_code_hashes=actual,checkpoint_provenance_sha256=SHA)
