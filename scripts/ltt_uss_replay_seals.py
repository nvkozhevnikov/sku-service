"""Explicit pinned historical/current replay provenance, with no default seal."""
from pathlib import Path
from scripts import run_ltt_uss_run1 as r

ROOT=r.BASE.parents[1]
PROVENANCE=r.BASE/'USS_SEAL_LOADER_RESUME_2026-10-15/SEAL_PROVENANCE.json'
PROVENANCE_SHA256='5c5a0fb3634d6e442d46e2672b6e1062224e479d324392e729bacdcc2b9cc0b4'
ORIGINAL='ORIGINAL_RUN1'
CURRENT='CURRENT_ACCEPTED_PRE_BATCH_SEAL'
PROPOSAL='TERMINAL_BATCH_FIX_PROPOSAL'
TERMINAL='TERMINAL_BATCH_FIX_CODE_SEAL'
PRE_KIND='CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL'
KIND_PROPOSAL='PROPOSED_PRODUCT_KIND_CODE_SEAL'
CHECKPOINT_PROVENANCE=r.BASE/'PRODUCT_KIND_29_RESUME_2026-10-16/CHECKPOINT_PROVENANCE.json'
CHECKPOINT_PROVENANCE_SHA256='d0212971ff0e1a113cfcd0e4b3bf6af0c712ab3482f2773b5e97db83c27b3e99'

def checkpoint_provenance():
    assert CHECKPOINT_PROVENANCE.is_file(),'REQUESTED_CHECKPOINT_PROVENANCE_MISSING'
    assert r.digest(CHECKPOINT_PROVENANCE.read_bytes())==CHECKPOINT_PROVENANCE_SHA256,'CHECKPOINT_PROVENANCE_REWRITTEN'
    p=r.load(CHECKPOINT_PROVENANCE)
    for key in ['old_provenance','terminal_batch_seal','baseline','plan']:
        pin=p[key];path=(ROOT/pin['path']).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.is_file(),'SEAL_EVIDENCE_MISSING'
        assert r.digest(path.read_bytes())==pin['sha256'],'SEAL_HISTORICAL_EVIDENCE_REWRITTEN'
    for sources in p['archives'].values():
        for item in sources.values():
            path=(ROOT/item['path']).resolve()
            assert path.is_relative_to((CHECKPOINT_PROVENANCE.parent/'ARCHIVED_CODE').resolve()) and path.is_file(),'REQUESTED_ARCHIVED_CODE_MISSING'
            assert r.digest(path.read_bytes())==item['sha256'],'SEAL_ARCHIVED_CODE_REWRITTEN'
    return p

def named_seal(name):
    if name=='FINAL_HOTSPOT_36_CODE_SEAL':
        from scripts.final_hotspot36_seals import named
        return named(name)
    if name in {'INVERSE_KIND_97_ACCEPTED_CODE_SEAL','DISK_ALIAS_10_CODE_SEAL'}:
        from scripts.disk_alias_seals import named
        return named(name)
    if name in {'PRODUCT_KIND29_ACCEPTED_CODE_SEAL','PROPOSED_INVERSE_KIND_97_CODE_SEAL'}:
        from scripts.inverse_kind_seals import named
        return named(name)
    if name not in {ORIGINAL,CURRENT,TERMINAL,PRE_KIND,KIND_PROPOSAL}:
        raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    p=checkpoint_provenance()
    assert name in p['archives'],'REQUESTED_NAMED_SEAL_MISSING'
    return {key:item['sha256'] for key,item in p['archives'][name].items()}

def provenance():
    assert PROVENANCE.is_file(),'SEAL_PROVENANCE_MISSING'
    assert r.digest(PROVENANCE.read_bytes())==PROVENANCE_SHA256,'SEAL_PROVENANCE_REWRITTEN'
    p=r.load(PROVENANCE)
    assert p['status']=='ACCEPTED_PRE_BATCH_PROVENANCE_VERIFIED' and p['PROVENANCE_CHAIN_VALID']=='YES'
    for pin in p['pins']:
        path=(ROOT/pin['path']).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.is_file(),'SEAL_EVIDENCE_MISSING'
        assert r.digest(path.read_bytes())==pin['sha256'],'SEAL_HISTORICAL_EVIDENCE_REWRITTEN'
    archived=checkpoint_provenance()['archives']
    for label,sources in p['reconstruction'].items():
        assert label in archived,'REQUESTED_NAMED_SEAL_MISSING'
        for key,item in sources.items():
            # Historical bytes are explicitly archived and pinned by the new
            # checkpoint; never reinterpret a working file as old evidence.
            assert key in archived[label] and archived[label][key]['sha256']==item['sha256'],'SEAL_ARCHIVED_CODE_REWRITTEN'
    return p

def baseline_evidence(baseline_seal):
    if baseline_seal=='DISK_ALIAS_10_CODE_SEAL':return named_seal(baseline_seal)
    if baseline_seal=='INVERSE_KIND_97_ACCEPTED_CODE_SEAL':return named_seal(baseline_seal)
    if baseline_seal=='PRODUCT_KIND29_ACCEPTED_CODE_SEAL':return named_seal(baseline_seal)
    if baseline_seal not in {ORIGINAL,CURRENT,TERMINAL,PRE_KIND}:
        raise ValueError('EXPLICIT_APPROVED_BASELINE_SEAL_REQUIRED')
    if baseline_seal in {TERMINAL,PRE_KIND}:return named_seal(baseline_seal)
    p=provenance()
    return p['original_code_hashes'] if baseline_seal==ORIGINAL else p['accepted_pre_batch_code_hashes']

def verify_active_code(*,baseline_seal=None,proposed_transition=None,working_hashes=None):
    if baseline_seal=='DISK_ALIAS_10_CODE_SEAL':
        from scripts.final_hotspot36_seals import verify
        return verify(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_hashes=working_hashes)
    if baseline_seal=='INVERSE_KIND_97_ACCEPTED_CODE_SEAL':
        from scripts.disk_alias_seals import verify
        return verify(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_hashes=working_hashes)
    if baseline_seal=='PRODUCT_KIND29_ACCEPTED_CODE_SEAL':
        from scripts.inverse_kind_seals import verify
        return verify(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_hashes=working_hashes)
    expected=baseline_evidence(baseline_seal)
    p=provenance()
    if baseline_seal==PRE_KIND:
        if proposed_transition is not None:
            assert proposed_transition==KIND_PROPOSAL,'UNAPPROVED_SEAL_TRANSITION'
            proposed=named_seal(KIND_PROPOSAL)
            assert set(expected)==set(proposed)
            allowed=checkpoint_provenance()['approved_transition']['changed_business_paths']
            assert sorted(k for k in expected if expected[k]!=proposed[k])==allowed,'UNAPPROVED_BUSINESS_CODE_DELTA'
            expected=proposed
        actual=r.code_hashes()
        actual['sterbrust_matching/product_identity.py']=r.digest((ROOT/'sterbrust_matching/product_identity.py').read_bytes())
        if working_hashes is not None:actual=working_hashes
        assert actual==expected,'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
        return dict(baseline_seal=baseline_seal,proposed_transition=proposed_transition,working_code_hashes=actual,
            checkpoint_provenance_sha256=CHECKPOINT_PROVENANCE_SHA256,historical_origin_sha256=p['original_seal']['sha256'])
    if proposed_transition is not None:
        assert baseline_seal==CURRENT and proposed_transition==PROPOSAL,'UNAPPROVED_SEAL_TRANSITION'
        assert p['allowed_proposal']==proposed_transition
        proposed=p['proposed_batch_code_hashes']
        assert set(proposed)==set(expected)
        assert [k for k in proposed if proposed[k]!=expected[k]]==['universal_supplier/adapters/uss_stanko.py']
        expected=proposed
    actual=r.code_hashes() if working_hashes is None else working_hashes
    assert actual==expected,'SELECTED_BASELINE_WORKING_CODE_MISMATCH'
    return dict(baseline_seal=baseline_seal,proposed_transition=proposed_transition,
                historical_origin_sha256=p['original_seal']['sha256'],
                accepted_pre_batch_sha256=p['accepted_pre_batch_seal']['sha256'],working_code_hashes=actual)
