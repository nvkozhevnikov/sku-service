"""Explicit accepted inverse97 -> disk10 code transition, immutable archives."""
from scripts.disk_alias_batch import S,B,r,o,AUTH_SHA
ACCEPTED='INVERSE_KIND_97_ACCEPTED_CODE_SEAL'
PROPOSED='DISK_ALIAS_10_CODE_SEAL'
PROVENANCE=S/'DISK_CODE_PROVENANCE.json'
SHA='2b62e5f5df01571b8a27065776ba9dd40f8d5a048781c581c5e588681f0519ad'

def provenance():
    assert PROVENANCE.is_file(),'DISK_PROVENANCE_MISSING'
    assert r.digest(PROVENANCE.read_bytes())==SHA,'DISK_PROVENANCE_REWRITTEN'
    p=r.load(PROVENANCE)
    assert p['authorization_sha256']==AUTH_SHA and r.digest((S/'AUTHORIZED_10_ROWS.json').read_bytes())==AUTH_SHA
    assert r.digest((B/'INVERSE_KIND_97_CODE_SEAL.json').read_bytes())==p['prior_code_seal_sha256']
    for label,folder in [('before','ARCHIVED_ACCEPTED_CODE'),('after','ARCHIVED_DISK_CODE')]:
      for n,sha in p[label].items():assert r.digest((S/folder/n).read_bytes())==sha,('DISK_ARCHIVE_DRIFT',label,n)
    return p

def named(name):
    if name not in {ACCEPTED,PROPOSED}:raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    return provenance()['before' if name==ACCEPTED else 'after']

def verify(*,baseline_seal,proposed_transition=None,working_hashes=None):
    assert baseline_seal==ACCEPTED,'EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED'
    before=named(ACCEPTED);expected=before
    if proposed_transition is not None:
      assert proposed_transition==PROPOSED,'UNAPPROVED_SEAL_TRANSITION'
      expected=named(PROPOSED);assert set(before)==set(expected)
      assert sorted(n for n in before if before[n]!=expected[n])==provenance()['changed_business_paths']
    actual={n:r.digest((o.ROOT/n).read_bytes()) for n in expected} if working_hashes is None else working_hashes
    assert actual==expected,'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
    return dict(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_code_hashes=actual,checkpoint_provenance_sha256=SHA)
