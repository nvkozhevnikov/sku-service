"""Read-only closeout and resumable final checkpoint for three operator cards."""
from pathlib import Path
import sys,json
from xml.etree import ElementTree
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.close_beka_operator_ledger import OUT,BASE,PREVIOUS,prepare,load,save,sha
from scripts.intervesp_full_preflight import read_source_state

def main():
    report=load(OUT/'APPLIED.json'); state=read_source_state()
    if json.loads(json.dumps(state,default=str))!=report['after']: raise ValueError('Post-replay SQL state drift')
    if report['stage']!='INGEST_AND_REPLAY_VERIFIED' or len(report['replay'])!=3 or not all(r['exact_noop'] for r in report['replay']): raise ValueError('Replay not verified')
    audits,pairs=prepare()
    if json.loads(json.dumps(audits))!=load(OUT/'SOURCE_IDENTITY_AUDIT.json'): raise ValueError('Operator/source ledger drift')
    if sha(PREVIOUS/'MATCHING_AFTER.json')!=report['prior_matching_sha256']: raise ValueError('Previous matching changed')
    result=OUT/'CANONICAL_VISIBLE_CONTEXT'; previous=load(PREVIOUS/'MATCHING_AFTER.json')['rows']; rows=load(result/'MATCHING_AFTER.json')['rows']
    if rows[:1826]!=previous or len(rows)!=1829: raise ValueError('Previous proposals changed or coverage mismatch')
    if len({(r['source'],str(r['external_id'])) for r in rows})!=len(rows): raise ValueError('Proposal duplicate')
    if {str(r['external_id']) for r in rows[-3:]}!={p.site_internal_id for p,c in pairs}: raise ValueError('Three source identity coverage mismatch')
    # Verify immutability by bytes only; no spreadsheets/XML publication.
    final_dir=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/FINAL_PROPOSALS'
    exports={n:h for n,h in load(final_dir/'FINAL_FILES_VERIFIED.json')['output_sha256'].items() if Path(n).suffix.lower() in ('.xlsx','.csv','.xml')}
    if len(exports)!=8 or any(sha(final_dir/n)!=h for n,h in exports.items()): raise ValueError('Final exports changed')
    if sha(Path(report['backup']['path']))!=report['backup']['sha256']: raise ValueError('Backup changed')
    tests=ROOT/'reports/RC_LOCAL/BEKA_OPERATOR_FOCUSED_TESTS_2026-10-01.xml'
    suites=ElementTree.parse(tests).getroot().findall('testsuite')
    test_result={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    if test_result!={'tests':98,'failures':0,'errors':0,'skipped':0}: raise ValueError('Focused test evidence not fully PASS')
    summary=load(result/'SUMMARY.json')
    summary.update(DONE='Three exact operator decisions applied; Beka29 source ledger closed',
        APPLIED='3 passive RU RC source products/offers/captures/observations; no canonical links',
        VERIFIED='LIVE-VERIFIED SQL identity/safety/replay; REPOSITORY-VERIFIED scoped proposals/98 focused tests',
        closed_source_ledger={'total':29,'resolved':27,'excluded':2,'pending_human_source_identity':0},
        source_namespace_counts={'beka_mak':109,'beka_mak_tr':78},
        focused_tests=test_result,focused_tests_sha256=sha(tests),backup=report['backup'],
        final_exports_unchanged=exports,no_global_matching=True,operator_equivalence_scope='three exact saved RU URLs only')
    guards=load(result/'CANONICAL_GUARD_AUDIT.json')
    lines=['# Beka operator ledger closure — 2026-10-01','',
        '## DONE / APPLIED / VERIFIED','',
        'DONE:three OPERATOR-CONFIRMED card equivalences applied, coolant-specific identity exclusion and operator404 GANTRY exclusion retained; source ledger closed27 resolved +2 excluded =29, no pending human source-identity decisions.',
        'APPLIED:3 new RU source products,3 inactive offers,3 saved-detail captures and3 append-only observations in isolated RC only. No canonical Sterbrust ID assigned or canonical SQL link created. Raw H1 and article retained; alias is not an independent model.',
        'LIVE-VERIFIED:PG17.11 /127.0.0.1:55449 /universal_supplier_server /system ID7691270601420084116; recoveryfalse and approved data path. Fresh preflight, logical backup, old product/offer PKs preserved, all3 exact-noop replays, counts unchanged by replay, duplicate/activation/link guards0.',
        'REPOSITORY-VERIFIED:98 focused tests PASS, zero failures/errors/skips. Previous1826 proposals preserved exactly; matching only3 new Beka source rows, scoped candidate retrieval and readiness checks, no global rerun. Eight existing final XML/XLSX/CSV byte hashes unchanged.','',
        '## Final counts','',
        '| Scope | Existing | READY_TO_CREATE_IDENTITY | Review | Conflict |',
        '|---|---:|---:|---:|---:|',
        '| Beka187 |62|0|123|2|',
        '| Commercial1829 |345|0|1223|261|','',
        'Beka namespaces:RU109,TR78. New3 decisions:0 Existing /0 READY /2 canonical Review /1 canonical Conflict. Source ledger closure does NOT claim canonical matching completion. Intervesp and all prior1826 decisions unchanged; Partner-ST/Optimum ingestion/matching not run.',
        'SQL before→after:source_products/offers4399→4402; captures/observations2523→2526; suppliers5, product_matches2890, catalog_products59427 unchanged. Commercial suppliers disabled, offers inactive, links/duplicates0.','',
        '## Three exact source decisions and independent canonical gates','']
    for row in rows[-3:]:
        applied=next(a for a in report['rows'] if str(a['external_id'])==str(row['external_id']))
        a=applied['product']['enrichment_evidence']['supplier_page_identity']
        checks=[g for g in guards if str(g['external_id'])==str(row['external_id'])]
        best=min(checks,key=lambda g:('FULL_MODEL_EXECUTION_MISMATCH' in g['blocking_reasons'],len(g['blocking_reasons'])),default=None)
        lines += ['### '+a['visible_primary_model'],'',
            a['url'],
            '- Visible article alias: '+'; '.join(dict.fromkeys(a['visible_article'])),
            '- Decision:OPERATOR-CONFIRMED exact card equivalence; primary visible model unchanged; original article and characteristics preserved.',
            '- Canonical proposal:'+row['classification']+'; no confirmed Sterbrust ID.',
            '- Original matcher diagnostics:'+json.dumps(row.get('match_conflicts'),ensure_ascii=False),
            '- Closest retrieved target strict blockers:'+json.dumps(best['blocking_reasons'] if best else ['NO_CANONICAL_CANDIDATE'],ensure_ascii=False),'']
    lines+=['The320GS NC CONFLICT is a canonical comparison against retrieved BMSO325C (proposed target17170), not the operator-resolved H1/article mismatch. The operator decision cannot prove these two different canonical models equivalent. No SQL link is written.440CDGS high-confidence candidate remains subject to full canonical/typed guards; no global NC suffix removal was added.','',
        '## Exclusions','',
        'Coolant exact URL:EXCLUDED_OPERATOR_INSUFFICIENT_IDENTITY, not a global consumables ban. GANTRY2200 XS exact URL:SOURCE_404_STALE_URL / OPERATOR-CONFIRMED; archived200 not current availability and404 not canonical absence. Neither URL inserted.','',
        '## Tests / changes / checkpoint','',
        'Focused98 tests:operator decisions, visible identity, offline review resolution, strict NEW grouping, execution evidence, commercial persistence. No full test suite.',
        'Changed code:universal_supplier/beka_operator_decisions.py; scripts/close_beka_operator_ledger.py; scripts/verify_beka_operator_closure.py; tests/test_beka_operator_decisions.py. Existing scoped match_beka_visible_identity.py parameterized for an isolated3-row scope, preserving previous output files. Existing adapters/global matcher unchanged.',
        'Knowledge updated:root CURRENT_STATE/ACCEPTANCE_MATRIX/active RC task and supplier source-identity workflow. All other working-tree changes preserved.',
        'LATEST matching:CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json. SOURCE_IDENTITY_AUDIT.json is the closed29 ledger. APPLIED.json contains before/after/replay and backup provenance. VERIFIED.json hashes every scoped output.',
        'resume_from: '+str(OUT/'CHECKPOINT.json'),
        'Resume next from remaining canonical blockers only; do not repeat source decisions,3 inserts, HTTP or global matching. Application script detects already-saved matching and refuses rerun. No operation is running after closeout.',
        'No HTTP/crawl/discovery, Partner-ST/Optimum job, full suite, XML publication, production/ESOL, scheduler, server start/stop or Git operation.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    summary['output_sha256']={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name not in ('VERIFIED.json','CHECKPOINT.json')}
    save(OUT/'VERIFIED.json',summary)
    save(OUT/'CHECKPOINT.json',{'stage':'DONE_APPLIED_VERIFIED','resume_from':str(OUT/'CHECKPOINT.json'),
        'next_scope':'remaining Beka canonical blockers only; source ledger CLOSED',
        'no_repeat_ingest':True,'no_http':True,'no_global_matching':True,'latest_matching':'CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json',
        'matching_sha256':sha(result/'MATCHING_AFTER.json'),'verified_sha256':sha(OUT/'VERIFIED.json'),
        'report':'REPORT.md','beka':summary['beka'],'commercial':summary['commercial'],'focused_tests_passed':98})
    print(json.dumps({k:summary[k] for k in ('DONE','APPLIED','VERIFIED','beka','commercial','closed_source_ledger','focused_tests')},ensure_ascii=True))

if __name__=='__main__': main()
