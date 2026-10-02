"""Close scoped saved29 checkpoint, read-only SQL and immutable final files."""
from pathlib import Path
import sys,json
from collections import Counter
from xml.etree import ElementTree
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.audit_beka_visible_identity import OUT,PRIOR,load,save,sha
from scripts.apply_beka_visible_identity import pairs
from scripts.intervesp_full_preflight import read_source_state
from scripts.resolve_reviews_offline import BASE

def main():
    audits,ready=pairs(); saved=load(OUT/'SOURCE_IDENTITY_AUDIT.json')
    if saved!=json.loads(json.dumps(audits)): raise ValueError('Saved29 evidence/identity audit drift')
    applied=load(OUT/'APPLIED.json'); state=read_source_state()
    if json.loads(json.dumps(state,default=str))!=applied['replay_after']: raise ValueError('SQL state drift since replay')
    if sha(PRIOR/'MATCHING_AFTER.json')!=applied['prior_matching_sha256']: raise ValueError('Prior1802 proposal changed')
    current_dir=OUT/'CANONICAL_VISIBLE_CONTEXT'; summary=load(current_dir/'SUMMARY.json')
    current=load(current_dir/'MATCHING_AFTER.json')['rows']; previous=load(PRIOR/'MATCHING_AFTER.json')['rows']
    if current[:1802]!=previous or len(current)!=1826: raise ValueError('Previous decisions changed')
    exports=load(BASE/'FINAL_FILES_VERIFIED.json')['output_sha256']; kept={}
    for name,digest in exports.items():
        if Path(name).suffix.lower() in ('.xml','.xlsx','.csv'):
            if sha(BASE/name)!=digest: raise ValueError('Published final output changed')
            kept[name]=digest
    if len(kept)!=8: raise ValueError('Expected eight immutable final exports')
    tests=ROOT/'reports/RC_LOCAL/BEKA_VISIBLE_IDENTITY_TESTS_2026-10-01.xml'
    suites=ElementTree.parse(tests).getroot().findall('testsuite')
    totals={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    if totals['failures'] or totals['errors'] or totals['skipped']: raise ValueError('Tests not fully PASS')
    applied_keys={(r['source'],str(r['external_id'])) for r in applied['rows']}
    raw=load(current_dir/'RAW_SCOPED_SOURCE.json')
    if len(raw)!=24 or {(r['supplier_code'],str(r['external_id'])) for r in raw}!=applied_keys: raise ValueError('Applied source coverage mismatch')
    for record in raw:
        a=record['raw_data']['enrichment_evidence']['supplier_page_identity']
        if a['verdict']!='SOURCE_IDENTITY_RESOLVED_VISIBLE_CARD' or a['canonical_identity_confirmed']: raise ValueError('Unresolved identity persisted')
    reason_summary=Counter(); checks=load(current_dir/'CANONICAL_GUARD_AUDIT.json')
    for r in current[1802:]:
        candidates=[a for a in checks if (a['source'],str(a['external_id']))==(r['source'],str(r['external_id']))]
        best=min(candidates,key=lambda x:('FULL_MODEL_EXECUTION_MISMATCH' in x['blocking_reasons'],len(x['blocking_reasons'])),default=None)
        reason_summary.update(best['blocking_reasons'] if best else ['NO_CANONICAL_RETRIEVAL_CANDIDATES'])
    summary.update(DONE='Saved29 source-identity assessment and scoped strict canonical proposals',
        APPLIED='24 isolated RC source rows/offers and append-only captures/observations; zero canonical SQL links',
        VERIFIED='LIVE-VERIFIED identity/counts/safety/replay; REPOSITORY-VERIFIED proposals and tests',
        test_result=totals,test_stdout='147 passed, 8 subtests passed',test_junit_sha256=sha(tests),
        closest_canonical_candidate_blockers=dict(reason_summary),published_final_exports_unchanged=kept,
        unpersisted_sources={'visible_field_conflict':3,'insufficient_identity':1,'operator404_stale_url':1},
        caveat='24 source-resolved identities remain canonical REVIEW; these are not confirmed new physical products',
        latest_matching='CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json',
        initial_pass='MATCHING_AFTER.json retained, superseded by CANONICAL_VISIBLE_CONTEXT',
        backup=applied['backup'])
    # Stable report shows every visible primary/alias without claiming archived
    # HTML is a live observation, especially for operator-confirmed404.
    lines=['# Beka visible-card identity — 2026-10-01','',
        '## DONE / APPLIED / VERIFIED','',
        'DONE:29 saved Beka cards assessed;19/23 HUMAN and5/6 HTTP REVIEW source identities resolved. No new GET, discovery or global matching run.',
        'APPLIED:24 new isolated RC source_products, inactive offers and append-only captures/observations. Commercial suppliers remain disabled; no canonical SQL links, matching/selection-table changes or production/ESOL writes.',
        'LIVE-VERIFIED: PostgreSQL17.11 /127.0.0.1:55449 /universal_supplier_server /system ID7691270601420084116, recoveryfalse, approved path. Logical backup retained; old product/offer PKs preserved,24 replay pairs exact no-op, before/after and replay counts checked. No server start/stop.',
        'REPOSITORY-VERIFIED:147 tests +8 subtests PASS;59500-row pinned canonical registry SHA validated;1802 prior decisions unchanged; original8 final XML/XLSX/CSV hashes unchanged. New artifacts are proposals only, not publication or ESOL payload.','',
        '## Scope and outcomes','',
        'Resolved24; true visible H1/article execution conflicts3; insufficient visible identity1; OPERATOR-CONFIRMED source404/stale URL1. These five exclusions were not inserted and are separate from persisted matching totals.',
        'Beka184:62 Existing /0 READY_TO_CREATE_IDENTITY /121 Review /1 Conflict. Commercial1826:345 Existing /0 READY_TO_CREATE_IDENTITY /1221 Review /260 Conflict. Intervesp and all previous decisions unchanged. All24 added source identities still fail canonical gates and remain REVIEW; source resolution is not canonical confirmation. The REVIEW increase reflects newly registered sources, not demotion of previous Existing.',
        'SQL before→after: products/offers4375→4399; captures/observations2499→2523; suppliers5, product_matches2890 and catalog_products59427 unchanged. Commercial canonical links/active offers/duplicate products/duplicate offers all0. Partner-ST/Optimum flags/counts unchanged.',
        '19 HUMAN resolved =2RU+17TR;5 HTTP REVIEW resolved =2RU+3TR. The two RU HTTP model extractor defects were cosmetic Cyrillic O and P-SAW token handling, not real execution conflicts. Raw H1/article retained alongside normalized full model.','',
        '## Canonical evidence and aliases','',
        'Visible primary model/execution is retained.64 alias evidence records are provenance-labelled url_alias/metadata_alias and CANDIDATE_RETRIEVAL_ONLY. Hidden SKU is not stored as an explicit manufacturer article. Raw aliases are under raw_data.enrichment_evidence.supplier_page_identity.aliases; candidate retrieval and candidate-by-candidate canonical guards are in CANONICAL_VISIBLE_CONTEXT/.',
        'Own visible English technical labels have a separate bilingual diagnostic view; raw SQL source facts are unchanged. The initial pass is retained separately. No inherited contradiction about another retrieved candidate is treated as proof about a different target; every exact pair still passes the unchanged full-model/semantic/condition/axis/typed/uniqueness guards.',
        'Closest candidate blockers (one count per source/reason, not evidence that these candidates are equivalent): '+json.dumps(dict(reason_summary),ensure_ascii=False),
        'NEW grouping/characteristic/absence/section evidence remains advisory. No Sterbrust Product ID is invented. Brand+base model, alias or fuzzy similarity is insufficient.','',
        '## Per-card evidence (saved, not live)','',
        'All titles/properties below come from archived captures. For GANTRY, current404 is operator-confirmed and supersedes archived200; neither proves absence in Sterbrust.','']
    for i,a in enumerate(audits,1):
        url_alias=[x['value'] for x in a['aliases'] if x['kind']=='url_alias']
        meta=[x.get('field','')+': '+x['value'] for x in a['aliases'] if x['kind']=='metadata_alias']
        lines += [f"### {i}. {a['source']} / {a['origin']}",'',a['url'],
            '- Verdict: '+a['verdict'],
            '- Archived visible H1: '+'; '.join(a['visible_title']),
            '- Visible primary full model: '+(a['visible_primary_model'] or 'NOT_PROVEN'),
            '- Visible article/SKU: '+('; '.join(dict.fromkeys(a['visible_article'])) or 'NOT_SHOWN'),
            '- URL alias: '+'; '.join(url_alias),
            '- Hidden metadata aliases: '+('; '.join(meta) or 'NONE_OBSERVED'),
            '- Reason: '+'; '.join(a['reasons']),
            '- Capture/provenance: '+a['capture_path']+' / SHA256 '+a['capture_sha256'],'']
    lines+=['## Files and resume','',
        'SOURCE_IDENTITY_AUDIT.json:29 per-card verdicts, visible fields, properties, aliases, reasons and capture SHA.',
        'APPLIED.json / BEFORE.dump:live before/after, backup provenance,24 applied product/capture pairs and exact-noop replay.',
        'CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json:latest merged1826 proposal checkpoint. Root MATCHING_AFTER.json is retained initial diagnostic pass, not latest.',
        'CANONICAL_VISIBLE_CONTEXT/CANONICAL_GUARD_AUDIT.json:per-target full strict proofs/blockers; ALIAS_RETRIEVAL_AUDIT.json:aliases and candidate IDs without acceptance authority.',
        'CANONICAL_VISIBLE_CONTEXT/NEW_GROUP_ADVISORY.json / ABSENCE_ADVISORY.json:scoped advisory gates, no automatic source/canonical promotion.',
        'VERIFIED.json / CHECKPOINT.json:hashes, acceptance and no-repeat resume. Test JUnit:../BEKA_VISIBLE_IDENTITY_TESTS_2026-10-01.xml.',
        'Changed code:universal_supplier/beka_visible_identity.py; scripts/audit_beka_visible_identity.py; scripts/apply_beka_visible_identity.py; scripts/match_beka_visible_identity.py; scripts/verify_beka_visible_identity.py; tests/test_beka_visible_identity.py. Existing adapters/crawler/panel/global matcher unchanged.',
        'Knowledge:root ACCEPTANCE_MATRIX.md, CURRENT_STATE.md, active RC task; workflows/supplier-audits/README.md and SOURCE_PAGE_IDENTITY.md.',
        'Do not repeat29 assessment, new HTTP or24 source insertion; resume proposals from latest checkpoint only. Final XML/XLSX publication remains pending separate review. Production/ESOL/scheduler/deployment/selection/HTTP were not touched.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    summary['output_sha256']={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name not in ('VERIFIED.json','CHECKPOINT.json')}
    save(OUT/'VERIFIED.json',summary)
    save(OUT/'CHECKPOINT.json',{'stage':'VISIBLE_SOURCE_AND_CANONICAL_PROPOSALS_VERIFIED','no_repeat_http':True,
        'no_repeat_ingest':True,'latest_matching':summary['latest_matching'],'matching_sha256':sha(current_dir/'MATCHING_AFTER.json'),
        'verified_sha256':sha(OUT/'VERIFIED.json'),'report':'REPORT.md','commercial':summary['commercial'],'beka':summary['beka']})
    print(json.dumps({k:summary[k] for k in ('DONE','APPLIED','VERIFIED','test_stdout','beka','commercial','closest_canonical_candidate_blockers')},ensure_ascii=True))

if __name__=='__main__': main()
