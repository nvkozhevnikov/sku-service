"""Seal hashes without redoing case matching or document assessment."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.integrate_targeted_nine import OUT,PRE,load,sha
from universal_supplier.priority_evidence_verification import apply_verified
TOP=OUT.parent
def save_once(p,v):
    b=json.dumps(v,ensure_ascii=False,indent=2).encode()
    if p.exists():assert p.read_bytes()==b
    else:p.write_bytes(b)
def main():
    before=load(PRE/'MATCHING_ACCEPTED.json')['rows'];after=load(OUT/'MATCHING_ACCEPTED.json')['rows']
    promotions=load(OUT/'PROMOTIONS.json');assert len(promotions)==1
    assert apply_verified(before,promotions)==after and apply_verified(after,promotions)==after
    assert (TOP/'CHECKPOINT.json').read_bytes()==(OUT/'CHECKPOINT.json').read_bytes()
    assert load(OUT/'POWER_ROLES_FINAL_VERIFIED.json')['one_promotion_identical_after_stricter_role_guard']
    assert all('S6' not in m['source_property'] for m in load(OUT/'ROLE_SAFE_TYPED_MAPPINGS.json'))
    supervision=load(OUT/'OFFICIAL_SUPERVISORY_GUARDS.json')
    assert [s['priority_index'] for s in supervision if s['full_current_evidence_confirmed']]==[58]
    for path,value in load(OUT/'INPUT_SHA256.json').items():assert sha(Path(path))==value
    code=['universal_supplier/offline_review_resolution.py','universal_supplier/targeted_evidence_verification.py',
        'universal_supplier/priority_evidence_verification.py','universal_supplier/characteristic_evidence.py',
        'scripts/integrate_targeted_nine.py','scripts/inspect_targeted_saved_documents.py','scripts/seal_targeted_nine.py',
        'scripts/verify_targeted_power_roles.py','scripts/hash_verify_targeted_nine.py','tests/test_targeted_evidence_verification.py']
    save_once(TOP/'CODE_SHA256.json',{p:sha(ROOT/p) for p in code})
    artifacts={p.relative_to(TOP).as_posix():sha(p) for p in TOP.rglob('*') if p.is_file() and p.name!='ARTIFACT_SHA256.json'}
    save_once(TOP/'ARTIFACT_SHA256.json',artifacts)
    assert all(sha(TOP/p)==v for p,v in load(TOP/'ARTIFACT_SHA256.json').items())
    assert all(sha(ROOT/p)==v for p,v in load(TOP/'CODE_SHA256.json').items())
    print('PASS: '+str(len(artifacts))+' artifact hashes;10 code hashes; all input SHA;1promotion and exact-noop replay;supervisory guards')
if __name__=='__main__':main()
