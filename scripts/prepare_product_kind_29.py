"""Freeze archived provenance and restore an exact existing historical bundle."""
from pathlib import Path
from scripts.audit_ltt_uss_product_kind import OUT as AUDIT, r, o, q

P=r.BASE/'PRODUCT_KIND_29_RESUME_2026-10-16'
PREVIOUS=r.BASE/'PRODUCT_KIND_BATCH_FIX_2026-10-16'

def main():
    assert not P.exists()
    # Before any changes, verify the complete last blocked evidence package.
    for name,sha in r.load(PREVIOUS/'FINAL_ARTIFACT_SEAL.json').items():
        assert r.digest((PREVIOUS/name).read_bytes())==sha
    proposal=r.load(PREVIOUS/'PRODUCT_KIND_BATCH_FIX_PROPOSED_CODE_SEAL.json')
    actual=r.code_hashes();actual['sterbrust_matching/product_identity.py']=r.digest((o.ROOT/'sterbrust_matching/product_identity.py').read_bytes())
    assert actual==proposal
    P.mkdir()
    r.save(P/'PARENT_CHECKPOINT.json',r.load(PREVIOUS/'FINAL_CHECKPOINT.json'))
    r.save(P/'BUSINESS_CODE_PIN.json',proposal)
    from scripts import ltt_uss_replay_seals as s
    old=r.load(s.PROVENANCE);assert r.digest(s.PROVENANCE.read_bytes())==s.PROVENANCE_SHA256
    archives={}
    def archive(label,code):
        archived={}
        for name,sha in code.items():
            choices=[o.ROOT/name,PREVIOUS/'BEFORE_CODE'/name]
            for sources in old['reconstruction'].values():
                if name in sources and sources[name]['sha256']==sha:choices.append(o.ROOT/sources[name]['path'])
            source=next((p for p in choices if p.is_file() and r.digest(p.read_bytes())==sha),None)
            assert source is not None,('HISTORICAL_BYTES_UNAVAILABLE',label,name)
            dest=P/'ARCHIVED_CODE'/label/name;r.atomic_bytes(dest,source.read_bytes())
            archived[name]=dict(path=str(dest.relative_to(o.ROOT)),sha256=sha)
        archives[label]=archived
        return code
    archive(s.ORIGINAL,old['original_code_hashes'])
    archive(s.CURRENT,old['accepted_pre_batch_code_hashes'])
    terminal=r.load(q.accepted.P/'TERMINAL_BATCH_FIX_CODE_SEAL.json')
    archive('TERMINAL_BATCH_FIX_CODE_SEAL',terminal)
    before=dict(terminal);before['sterbrust_matching/product_identity.py']=r.load(PREVIOUS/'PREFLIGHT.json')['classifier_sha256']
    archive('CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL',before)
    archive('PROPOSED_PRODUCT_KIND_CODE_SEAL',proposal)
    old_harness=['scripts/ltt_uss_replay_seals.py','tests/test_replay_seal_loader.py','tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py','scripts/diagnose_product_kind_batch_impact.py']
    for name in old_harness:r.atomic_bytes(P/'BEFORE_HARNESS'/name,(o.ROOT/name).read_bytes())
    provenance=dict(format='EXPLICIT_PRODUCT_KIND_PROVENANCE_V1',old_provenance=dict(path=str(s.PROVENANCE.relative_to(o.ROOT)),sha256=s.PROVENANCE_SHA256),
        original_run1_seal=old['original_seal'],terminal_batch_seal=dict(path=str((q.accepted.P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').relative_to(o.ROOT)),sha256=r.digest((q.accepted.P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').read_bytes())),
        archives=archives,approved_transition=dict(baseline='CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL',proposal='PROPOSED_PRODUCT_KIND_CODE_SEAL',changed_business_paths=sorted(k for k in before if before[k]!=proposal[k])),
        baseline=dict(path=str(q.BASELINE.relative_to(o.ROOT)),sha256=r.digest(q.BASELINE.read_bytes())),
        plan=dict(path=str(q.PLAN.relative_to(o.ROOT)),sha256=r.digest(q.PLAN.read_bytes())),
        proposed_code_not_yet_accepted=True)
    assert set(provenance['approved_transition']['changed_business_paths'])=={'sterbrust_matching/product_identity.py','universal_supplier/adapters/ltt.py','universal_supplier/adapters/ltt_uss_html.py'}
    r.save(P/'CHECKPOINT_PROVENANCE.json',provenance)
    # Restore byte-for-byte historical artifact, never synthesize test data.
    source=Path(r'D:\documents\personal\ChatGPT\Стербруст\work\sku-service/reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED')
    summary=r.load(source/'BUILD_VERIFIED.json')
    assert summary['ready_to_create_real_records']==0 and summary['usable_confirmed_pairs']==119
    for name,sha in summary['output_sha256'].items():assert r.digest((source/name).read_bytes())==sha
    dest=o.ROOT/'reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED'
    assert not dest.exists()
    for path in source.rglob('*'):
        if path.is_file():r.atomic_bytes(dest/path.relative_to(source),path.read_bytes())
    assert (dest/'BUILD_VERIFIED.json').read_bytes()==(source/'BUILD_VERIFIED.json').read_bytes()
    for name,sha in summary['output_sha256'].items():assert r.digest((dest/name).read_bytes())==sha
    r.save(P/'HISTORICAL_BUNDLE_RECOVERY.json',dict(status='EXACT_EXISTING_HISTORICAL_BUNDLE_RECOVERED',source=str(source),destination=str(dest),
        build_verified_sha256=r.digest((source/'BUILD_VERIFIED.json').read_bytes()),output_sha256=summary['output_sha256'],
        ready_to_create_real_records=0,usable_confirmed_pairs=119,replacement_generated=False,non_gating_historical_environment=0))
    targets=r.load(AUDIT/'PRODUCT_KIND_CONFIRMED_DEFECTS.json')['rows']
    extra=r.load(PREVIOUS/'ADDITIONAL_IMPACT_MSK318.json')['source_evidence']['inventory']
    extra.update(expected_conceptual_kind='tooling/saw_blade',primary_cluster='HOST_MACHINE_TOKEN_OVERRIDES_SOLD_OBJECT',
        semantic_decision='CONFIRMED_FALSE_MACHINE_TOOLING',operator_authorization='1859bb3e-e1fc-45b3-9261-e9df796ccfa4')
    targets.append(extra);assert len(targets)==29
    r.save(P/'AUTHORIZED_29_ROWS.json',dict(count=29,msk=17,sib=12,rows=targets,msk318_and_msk40_distinct=True))
    print('Prepared explicit archived provenance:',r.digest((P/'CHECKPOINT_PROVENANCE.json').read_bytes()),flush=True)

if __name__=='__main__':main()
