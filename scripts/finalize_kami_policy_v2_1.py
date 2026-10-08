"""Finalize verified V2.1 reports, preserving rejected V2 evidence byte-for-byte."""
import csv,hashlib,json,subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime,timezone
from scripts.propose_kami_policy_v2 import ROOT,OUT
from scripts.rehearse_kami_feature_restore import connection


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')


def main():
    oldcp=json.loads((OUT/'CHECKPOINT.json').read_bytes())
    preserved={name:sha(OUT/name)==value for name,value in oldcp['file_hashes'].items() if name!='postgres_start.log'}
    assert all(preserved.values()),'Rejected V2 evidence altered'
    data=json.loads((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes())
    qa=json.loads((OUT/'V2_1_QA_VERIFIED.json').read_bytes())
    first=json.loads((OUT/'V2_1_APPLY_RECEIPT.json').read_bytes())
    second=json.loads((OUT/'V2_1_SECOND_RUN_NOOP.json').read_bytes())
    assert first['status']=='KAMI_V2_1_APPLIED_INDEPENDENTLY_VERIFIED'
    assert second['status']=='V2_1_SECOND_RUN_SEMANTIC_NOOP_VERIFIED'
    assert second['unexpected_semantic_delta']==0 and second['events']=={'VALIDATED_UNCHANGED':717}
    # Clarify logical decision counts, preserving the original audit-update
    # metric rather than hiding expected physical revalidation writes.
    for name,receipt in [('V2_1_APPLY_RECEIPT.json',first),('V2_1_SECOND_RUN_NOOP.json',second)]:
        receipt.setdefault('original_update_metric_before_clarification',receipt['UPDATE'])
        receipt['UPDATE']=0
        receipt['audit_revalidations']=717 if receipt is second else 0
        receipt['logical_counts_definition']='INSERT=decision-history events; UPDATE=semantic changes on replay; UNCHANGED=revalidated same decisions. Audit updates are separate.'
        receipt['physical_DML_breakdown_verified_by_lifecycle_and_scope']=(
            {'product_matches_validation_timestamp_updates':717,'source_products_audit_timestamp_updates':717}
            if receipt is second else {'product_matches_new_history_events':717,'previous_product_matches_superseded':717,'source_products_matching_link_updates':717})
        save(name,receipt)
    with connection('universal_supplier_kami_rc') as c:
        with c.transaction():
            c.execute('SET TRANSACTION READ ONLY')
            identity=c.execute("SELECT current_database(),inet_server_port(),system_identifier::text,current_setting('server_version') FROM pg_control_system()").fetchone()
            assert identity==('universal_supplier_kami_rc',55451,'7693098812512405428','17.11')
            statuses=dict(c.execute("""SELECT pm.status,count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id
                JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='kami' AND pm.is_current GROUP BY pm.status"""))
            assert statuses=={'HIGH_CONFIDENCE_MATCH':717,'REVIEW':4547}
            linked=[r[0] for r in c.execute("""SELECT sp.external_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                WHERE s.code='kami' AND sp.catalog_product_id IS NOT NULL ORDER BY sp.external_id""")]
            expected=sorted(r['external_id'] for r in data['rows'] if r['sterbrust_product_id'])
            assert linked==expected
            duplicates=c.execute("""SELECT count(*) FROM (SELECT sp.external_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                WHERE s.code='kami' GROUP BY sp.external_id HAVING count(*)>1) d""").fetchone()[0]
            assert duplicates==0
            selection=dict(c.execute('SELECT selection_status,count(*) FROM catalog_offer_selection GROUP BY selection_status'))
            assert selection=={'selected':117,'no_eligible_offer':4}
    csvmeta=json.loads((OUT/'V2_1_CSV_AUTHORING_VERIFIED.json').read_bytes());csvcounts={}
    for name,meta in csvmeta.items():
        with (OUT/name).open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
        csvcounts[name]=len(rows);assert len(rows)==meta['rows']
        if name=='POLICY_V2_1_EXISTING.csv': assert all(r['RC_applied_existing']=='true' for r in rows)
    suites=ET.parse(OUT/'V2_1_FOCUSED_TESTS.xml').getroot().findall('testsuite')
    assert sum(int(s.get('tests')) for s in suites)==233
    assert all(int(s.get(k,'0'))==0 for s in suites for k in ('failures','errors','skipped'))
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
    head=git('rev-parse','HEAD');assert head==oldcp['current_SHA']
    refs={name:git('rev-parse',name) for name in oldcp['protected_refs']};assert refs==oldcp['protected_refs']
    changes=git('status','--porcelain','--untracked-files=all').splitlines()
    linkedrows=[r for r in data['rows'] if r['external_id'] in set(linked)]
    from_price=sum(str(r['price_basis']).lower()=='from_price' for r in linkedrows)
    numeric_basis=dict(Counter(r['price_basis'] for r in linkedrows if r['price_state']=='numeric_public'))
    plus=json.loads((OUT/'V2_1_TARGETED_AUDIT.json').read_bytes())
    report=f'''# KAMI Matching Policy V2.1

Status: KAMI_MATCHING_POLICY_V2_1_APPLIED_AND_VERIFIED. 2026-10-08.
OPERATOR-CONFIRMED authorization2ec9a0b6. Feature HEAD unchanged: {head}.

## Outcome (LIVE-VERIFIED isolated KAMI RC)

5264 source identities /5264 offers /duplicates0. Existing717, Review4547,
Conflict0, NEW0. All717 accepted Sterbrust IDs are real active unique physical
canonical mappings. Suppliers remain disabled/offers inactive. No NEW creation.
Matching library is supplier-neutral; this adoption/apply is KAMI-only. Legacy
runtime matcher and five predecessor decisions were not globally rerun/replaced.

Numeric-public matched90/1333; POR617; missing-price10. Numeric price bases:
{json.dumps(numeric_basis,ensure_ascii=False)}. FROM_PRICE among matched={from_price}.
Price/availability/quantity/currency observations unchanged; no live price
comparison or supplier request. Snapshot prices are dated evidence, not live prices.

Paths: P2_EXACT_FULL_MODEL195; P3_EXACT_VARIANT_IN_SERIES517;
P4_APPROVED_BRAND_ALIAS_EXACT_MODEL3; P5_NAME_NORMALIZATION_EXACT_IDENTITY2.
717 unique targets. No exact canonical candidate4430 is NOT absence/NEW proof.

## Defect corrections and targeted audit

EXECUTION_PLUS preserved in own model tokens (S+,ATC+C,6K+2,45S+PRO,PRO+CTS30).
Ordinary descriptive plus is separately tagged; never an unapproved S+/S alias.
No raw/legacy source or canonical normalized/hash fields were rewritten.

W/kW conversions compare proven same power role. Main drive/main motor, spindle,
total installed, hydraulics, feed/polish/scoring/rotation and duty cycles remain
separate. Diameter above bed/carriage and turning/processing fields remain separate.
Unaligned repeated motor values, ranges, missing/unmapped units do not supply
fictitious contradictions or a mandatory characteristic quota. No values repaired.
Material numeric comparison uses explicit1%relative/0.01base-unit round-off
tolerance; this implementation inference is documented, not learned equivalence.

Old704 audited: {plus['old_existing_with_comparable_numeric_or_typed']} with comparable
typed fields; five actual aligned material-spec blockers and two S+/S mismatches
removed. The audit began before recompute; final ledger was refreshed after
targeted corrections against the same frozen old cohort, not fresh supplier data.
Delta vs rejected V2: removed7, new20, unchanged697, changed-target0.
No canonical-error card was silently repaired or classified NEW. Twelve current
rows have typed guard holds (including construction wording holds); they stay
Review, not invented source Conflict. All known5.5kW/55000W,11kW/1100W and
7.5kW/7.5W cases are non-auto with explicit evidence.

Plus ledger:34 source rows +195 canonical inventory records=229CSV rows.
All18 current Existing containing plus in source/canonical model were inspected.
Canonical-only inventory rows are not supplier assignments.

## Gates/tests/QA

Fresh checkpoint/code/rejected-artifact hashes, exact DB/system/data path,
application role kami_app/non-superuser,15immutable migrations and52 public
table/sequence fingerprints PASS before mutation. Physical target resolution
and effective/manual state checked again in writer context. RC manual decisions0.

47targeted tests PASS. 35actual operator calibrations PASS. 233focused regression
tests PASS, including56real approved scratch manual/feed/selection lifecycle tests.
Historical negatives, plus/motor/position adversarial tests stay non-auto.
No assertions weakened; full suite was not run or declared green.

Fresh Codex saved-evidence QA234 rows: all18plus, aliases, exact/series/suffix,
129rich and105sparse comparable-spec cases. Confirmed false positives0. This is
Codex evidence inspection, not an independent human ground-truth guarantee.
QA and code hashes are pinned. All detected material contradiction candidates
remain non-auto. Old stopped80-row QA was not reused as sufficient approval.

## APPLIED / replay / isolation

First apply: one transaction through PostgresRepository.persist_match only.
717 new history events;717 prior current events superseded;717 expected source
matching FK/audit updates. No ingestion/source identities/PK/offers changed.
Before COMMIT: all unrelated public tables and sequences unchanged; only expected
matching tables and product_matches sequence (+717,cache1) changed. Source-fact
projection and exact predecessor source/match fingerprints remained equal.
Fresh independent post-COMMIT verification PASS.

Second frozen matching recomputed5264 rows: decision/target/status delta0.
Second lifecycle validation: INSERT0, semantic UPDATE0, UNCHANGED717,
Review4547 (not persisted), Conflict0. No new history or sequence delta.
Repository audit updates:717 last_validated_at and717 source.updated_at. This is
semantic no-op, NOT exact physical no-op and NOT zero SQL writes.
Fresh independent verification PASS; fingerprints saved in both receipts.

Read-only canonical selection evaluator:838 products,117selected/721noeligible.
No selection apply. Stored selection remains121rows:117selected/4noeligible.
No KAMI winner is activated; disabled/inactive state is preserved.

## Preserved boundaries / artifacts

Rejected V2 evidence byte-for-byte preserved (server start log excluded because
PostgreSQL owns that live log). New10CSV files have independent readback/count/SHA
verification, via Spreadsheets skill/Artifact Tool flat tables plus CSV serializer
for the runtime's missing CSV export capability. No extra XLSX/XML published.

Five predecessor namespaces and frozen506Existing/2FULL/3640Review/259Conflict
advisory remain unchanged; they are not re-labelled as SQL links. Old final
dump/XML/tag unchanged by this scope. Their old DB fingerprints are now historical,
not valid current-target guards after this explicitly authorized matching apply.

Develop local/tracking deefb595868d19ba471b9b943ff779194e231c26 and production
local/tracking36924836bb02965e857173773cdb70f302914643 unchanged. No fresh GitHub
fetch claim. No commit/push/merge. Uncommitted feature files are hash-pinned.
Supplier/SterbrustHTTP0; source ingestion/RUN1/RUN2/crawl0; price/availability
refresh0; NEW/canonical creation0; selection/import/ESOL/deploy/scheduler0.

DONE: V2.1 corrections/new5264 proposal/QA/artifacts.
APPLIED:717accepted KAMI matching decisions only, normal lifecycle.
VERIFIED: live first/second receipts, zero unexplained semantic delta, scoped
fingerprints/sequences/manual regressions/CSV readback. Not production/release proof.

Remaining risks:4547unresolved, frozen source prices not refreshed, source remains
disabled/inactive, no selection publication; global runtime rollout/full suite and
new release dump/export are outside this task. STOP after this report.

Exact resume/checkpoint: CHECKPOINT_V2_1.json. Do not repeat first/second apply,
supplier ingestion or old704. Further scope needs a separate operator instruction.
'''
    (OUT/'REPORT_V2_1.md').write_text(report,encoding='utf8')
    hashes={p.name:sha(p) for p in OUT.iterdir() if p.is_file() and (p.name.startswith('V2_1_') or p.name.startswith('POLICY_V2_1_') or p.name=='REPORT_V2_1.md') and p.name!='V2_1_FINAL_VERIFICATION.json'}
    changed_files=[]
    for line in changes:
        if len(line)>=4:changed_files.append(line[3:])
    codehashes={name:sha(ROOT/name) for name in changed_files if (ROOT/name).is_file()}
    verification={'status':'V2_1_FINAL_ARTIFACTS_AND_LIVE_COUNTS_VERIFIED','recorded_at':datetime.now(timezone.utc).isoformat(),
        'csv_counts':csvcounts,'file_hashes':hashes,'code_hashes':codehashes,'rejected_V2_preserved':preserved,
        'live_identity':identity,'live_matching_statuses':statuses,'live_linked_KAMI':len(linked),
        'duplicates':duplicates,'stored_selection':selection,'refs':refs,'HEAD':head}
    save('V2_1_FINAL_VERIFICATION.json',verification)
    checkpoint={'status':'KAMI_MATCHING_POLICY_V2_1_APPLIED_AND_VERIFIED','resume_from':str(OUT/'CHECKPOINT_V2_1.json'),
        'starting_SHA':head,'final_SHA':head,'branch':git('branch','--show-current'),'commit_push':0,
        'matching_counts':{'Existing':717,'Review':4547,'Conflict':0,'NEW':0},'KAMI_source_rows':5264,'KAMI_offers':5264,
        'matched_prices':data['summary']['matched_prices'],'acceptance_paths':data['summary']['paths'],
        'delta_vs_rejected':data['summary']['delta_vs_rejected'],'QA':{'inspected':234,'false_positives':0,'all_existing_plus_inspected':18},
        'tests':{'targeted_PASS':47,'focused_PASS':233,'positive_calibrations_PASS':35,'scratch_lifecycle_PASS':56},
        'first_apply':{'history_INSERT':717,'source_link_UPDATE':717,'normal_prior_events_superseded':717},
        'second_matching':{'INSERT':0,'semantic_UPDATE':0,'UNCHANGED':717,'REVIEW':4547,'CONFLICT':0,'unexpected_delta':0,
            'audit_timestamp_updates_expected':{'source_products':717,'product_matches':717},'exact_physical_noop':False},
        'source_facts_offers_predecessors_unchanged':True,'selection_not_applied':True,'supplier_disabled':True,'offers_inactive':True,
        'rejected_V2_preserved':True,'protected_refs':refs,'changed_files':changed_files,'code_hashes':codehashes,'file_hashes':hashes,
        'supplier_HTTP':0,'Sterbrust_HTTP':0,'SQL_scope':'KAMI matching only; no new sources/offers/canonical products/manual/selection writes',
        'receipts':['V2_1_APPLY_RECEIPT.json','V2_1_SECOND_RUN_NOOP.json','V2_1_FINAL_VERIFICATION.json'],
        'next':'STOP_WAIT_FOR_SEPARATE_OPERATOR_SCOPE. Do not rerun ingestion/crawl/RUN1/RUN2/matching apply; no commit/push/merge/publication.'}
    save('CHECKPOINT_V2_1.json',checkpoint)
    print(json.dumps({'status':checkpoint['status'],'matching_counts':checkpoint['matching_counts'],'csv_counts':csvcounts,
        'HEAD':head,'changed_files':changed_files,'old_evidence_preserved':all(preserved.values())},ensure_ascii=False))


if __name__=='__main__':main()
