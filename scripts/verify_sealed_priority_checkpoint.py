"""Read-only checkpoint/replay checks, then pin output/code manifests once."""
from pathlib import Path
from collections import Counter
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_priority_near_complete import PRE,OUT,load,save,sha
from universal_supplier.priority_evidence_verification import apply_verified

def main():
    before=load(PRE/'MATCHING_AFTER.json')['rows'];after=load(OUT/'MATCHING_ACCEPTED.json')['rows']
    promotions=load(OUT/'ALL_PROMOTIONS.json');audit=load(OUT/'VERIFY_45.json')
    assert len(promotions)==51 and len(audit)==45 and all(a['accepted'] for a in audit)
    assert apply_verified(before,promotions)==after and apply_verified(after,promotions)==after
    assert Counter(r['classification'] for r in after)=={'EXISTING_CONFIRMED':505,'REVIEW':3643,'CONFLICT':259}
    previous={(r['source'],r['external_id']):r for r in load(PRE/'STRICT_EVALUATIONS.json')}
    for case in load(OUT/'INDEPENDENT_22_PROOF.json'):
        if case['priority_index'] not in range(47,53):continue
        a=case['assessments'][0];c=a['candidates'][0]
        old=next(x for x in previous[a['source'],a['external_id']]['evaluations'] if x['canonical_id']==c['canonical_id'])
        contradictions=old['evidence']['typed_characteristics']['contradictions']
        assert contradictions and all(p.get('key')=='spindle_bore' and p.get('reason')=='canonical_values_disagree' for p in contradictions)
    for p in promotions:
        proof=p['proof'];assert proof['confirmed'] and not proof['blocking_reasons']
        typed=proof['evidence']['typed_characteristics']
        assert not typed['contradictions'] and typed['independent_identity_support']
        assert len({p['key'] for p in typed['agreements']})>=2
        assert not proof['evidence']['axis']['blocked'] and not proof['evidence']['condition']['blocked']
        assert all(not(s['quality'] or s['conflicts'] or s['warnings']) for s in proof['evidence']['semantic_checks'])
    routes=load(OUT/'TERMINAL_22_ROUTES.json')
    assert Counter(r['route'] for r in routes)=={'CANONICAL_SIDE_EVIDENCE_REQUIRED':6,'RESOLVED_EXISTING':6,'SUPPLIER_SIDE_EVIDENCE_REQUIRED':3,'FACTUAL_CONFLICT':7}
    assert len({r['priority_index'] for r in routes})==22
    code=['universal_supplier/characteristic_evidence.py','universal_supplier/priority_evidence_verification.py',
          'scripts/verify_priority_near_complete.py','scripts/inspect_priority_terminal_evidence.py',
          'scripts/seal_priority_near_complete.py','scripts/verify_sealed_priority_checkpoint.py',
          'tests/test_priority_evidence_verification.py']
    save('CODE_SHA256.json',{p:sha(ROOT/p) for p in code})
    outputs={p.name:sha(p) for p in OUT.iterdir() if p.is_file() and p.name!='ARTIFACT_SHA256.json'}
    save('ARTIFACT_SHA256.json',outputs)
    assert all(sha(OUT/p)==s for p,s in load(OUT/'ARTIFACT_SHA256.json').items())
    assert all(sha(ROOT/p)==s for p,s in load(OUT/'CODE_SHA256.json').items())
    # No raw-registry reparse/global reevaluation: integrity hash only.
    assert all(sha(Path(p))==s for p,s in load(OUT/'INPUT_SHA256.json').items())
    print('PASS:51promotions;4407immutable identities;replay exact no-op;22exclusive routes;'+str(len(outputs))+' output hashes; all input/code hashes')
if __name__=='__main__':main()
