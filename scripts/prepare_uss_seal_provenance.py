"""Identify/pin immutable accepted origin BEFORE editing the replay loader."""
from scripts import uss_terminal_batch_fix as b
r,o=b.r,b.o
P=r.BASE/'USS_SEAL_LOADER_RESUME_2026-10-15'

def main():
    assert not P.exists();b.history()
    batchseal=b.PHASE/'NEW_CODE_SEAL_NOT_ACCEPTED.json'
    assert r.code_hashes()==r.load(batchseal)
    a=b.accepted;original=r.load(r.OUT/'CODE_SEAL.json');commercial=r.load(a.f.PHASE/'NEW_CODE_SEAL.json');h2=r.load(a.h.PHASE/'NEW_CODE_SEAL.json');accepted=r.load(a.PHASE/'NEW_CODE_SEAL.json')
    assert original==r.load(r.OUT/'CHECKPOINT.json')['code_hashes']
    assert original==r.load(a.f.PHASE/'OLD_CODE_SEAL.json')
    assert commercial==r.load(a.h.PHASE/'OLD_CODE_SEAL.json')
    assert h2==r.load(a.PHASE/'OLD_CODE_SEAL.json')
    assert accepted==r.load(b.PHASE/'OLD_CODE_SEAL.json')
    result=r.load(a.PHASE/'RUN2/RESULT.json');baseline=r.load(b.q.BASELINE)
    assert result['status']=='LTT_USS_H2_TITLE_FIX_AND_RUN2_REVERIFIED'
    assert result['run2_scope']==2634 and result['run2_exact_noop_persisted']==2445 and result['run2_terminal_delta']==0
    assert baseline['totals']==dict(source_products=2445,offers=2445,supplier_http_captures=2525,offer_commercial_observations=2525)
    assert baseline['sources']['uss_stanko']==dict(PERSISTED=219,AMBIGUOUS=122,MULTI_VARIANT=66)
    assert baseline['new_code_hashes']==accepted
    assert r.digest((a.PHASE/'NEW_CODE_SEAL.json').read_bytes())=='ac2117eec4d17759afc84928fb259d5ffc498137b098cfdcc80e86676c1f8056'
    pins=[]
    def pin(p):
        item=dict(path=str(p.relative_to(o.ROOT)),sha256=r.digest(p.read_bytes()));pins.append(item);return item
    originalpin=pin(r.OUT/'CODE_SEAL.json');pin(r.OUT/'CHECKPOINT.json');pin(r.OUT/'MANIFEST_SEAL.json')
    acceptedpin=pin(a.PHASE/'NEW_CODE_SEAL.json');pin(a.PHASE/'FINAL_CHECKPOINT.json');pin(a.PHASE/'RUN2/RESULT.json');pin(b.q.BASELINE);pin(b.q.PLAN)
    for phase in [a.f.PHASE,a.h.PHASE,a.PHASE]:
        for name in ['TRANSITION.json','OLD_CODE_SEAL.json','NEW_CODE_SEAL.json']:pin(phase/name)
    for p in [a.f.PHASE/'OLD_USS_ADAPTER.py',a.PHASE/'OLD_CARD_GATE.py',b.PHASE/'OLD_USS_ADAPTER.py',batchseal,b.PHASE/'FINAL_CHECKPOINT.json',b.PHASE/'FINAL_ARTIFACT_SEAL.json']:pin(p)
    # Reconstruct original/pre-batch file hashes from preserved archives and
    # unchanged working files; proposed business code is already pinned above.
    reconstruction={}
    for label,seal in [('ORIGINAL_RUN1',original),('CURRENT_ACCEPTED_PRE_BATCH_SEAL',accepted)]:
        sources={}
        for k,v in seal.items():
            if label=='ORIGINAL_RUN1' and k==b.ADAPTER:p=a.f.PHASE/'OLD_USS_ADAPTER.py'
            elif label=='ORIGINAL_RUN1' and k=='universal_supplier/ltt_uss_capture.py':p=a.PHASE/'OLD_CARD_GATE.py'
            elif label=='CURRENT_ACCEPTED_PRE_BATCH_SEAL' and k==b.ADAPTER:p=b.PHASE/'OLD_USS_ADAPTER.py'
            else:p=o.ROOT/k
            assert r.digest(p.read_bytes())==v;sources[k]=dict(path=str(p.relative_to(o.ROOT)),sha256=v)
        reconstruction[label]=sources
    proposal=r.load(batchseal);assert [k for k in proposal if proposal[k]!=accepted[k]]==[b.ADAPTER]
    provenance=dict(status='ACCEPTED_PRE_BATCH_PROVENANCE_VERIFIED',original_seal=originalpin,accepted_pre_batch_seal=acceptedpin,original_code_hashes=original,accepted_pre_batch_code_hashes=accepted,proposed_batch_code_hashes=proposal,
        allowed_proposal='TERMINAL_BATCH_FIX_PROPOSAL',proposal_status='PINNED_NOT_ACCEPTED_TESTS_AND_IMPACT_REQUIRED',pins=pins,reconstruction=reconstruction,
        chain=[dict(stage='ORIGINAL_RUN1',accepted=True,code_hashes=original),dict(stage='COMMERCIAL_TABLE_CORRECTION',accepted=True,code_hashes=commercial),dict(stage='H2_TITLE_LOCAL_FIX',accepted=False,status='HISTORICAL_FAILED_FOCUSED_GATE_PRESERVED',code_hashes=h2),dict(stage='H2_TITLE_WITH_SOURCE_URL_HANDOFF',accepted=True,status=result['status'],code_hashes=accepted),dict(stage='CURRENT_ACCEPTED_PRE_BATCH_SEAL',accepted=True,code_hashes=accepted),dict(stage='TERMINAL_BATCH_FIX_PROPOSAL',accepted=False,code_hashes=proposal)],
        run2=dict(scope=2634,exact_noop=2445,terminal_delta=0),baseline=baseline['totals'],PROVENANCE_CHAIN_VALID='YES',checked_at=r.now())
    r.save(P/'SEAL_PROVENANCE.json',provenance)
    r.save(P/'CURRENT_ACCEPTED_SEAL.json',dict(**acceptedpin,code_hashes=accepted,baseline=provenance['baseline'],run2=provenance['run2']))
    r.save(P/'LEGACY_RUN1_SEAL_PROOF.json',dict(**originalpin,checkpoint_code_hashes_equal=True,reconstructed_sources=reconstruction['ORIGINAL_RUN1'],historical_guard_preserved=True))
    for name in ['scripts/run_ltt_uss_run2_offline.py','scripts/qa_ltt_uss_frozen_witness.py','tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py']:
        r.atomic_bytes(P/'OLD_HARNESS'/name,(o.ROOT/name).read_bytes())
    print(dict(ORIGINAL_RUN1_SEAL=originalpin,CURRENT_ACCEPTED_PRE_BATCH_SEAL=acceptedpin,PROVENANCE_CHAIN_VALID='YES',provenance_sha256=r.digest((P/'SEAL_PROVENANCE.json').read_bytes())))

if __name__=='__main__':main()
