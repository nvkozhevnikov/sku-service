"""Evidence report and feature allowlist, never DB/crawl/selection/import code."""
import hashlib,json,os,re,subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'
BASE='c0135eef1ad1a2bd502fdc1ade62887974e7c222';PROD='36924836bb02965e857173773cdb70f302914643'
ALLOWED=['docs/MATCHING_POLICY_V22_AI_REVIEW.md','universal_supplier/matching_policy_v2.py',
 'universal_supplier/matching_policy_v22.py','tests/test_matching_policy_v22.py','tests/fixtures/matching_v22_kind_calibrations.json',
 'scripts/snapshot_matching_v22.py','scripts/analyze_matching_v22.py','scripts/dryrun_matching_v22.py',
 'scripts/package_matching_v22_review.py','scripts/export_matching_v22_csv.mjs','scripts/freeze_matching_v22_fixtures.py',
 'scripts/verify_matching_v22_review.py','scripts/finalize_matching_v22_review.py']
def git(*a):return subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
def save(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    assert git('branch','--show-current')=='feature/universal-supplier-matching-v2-2-ai-review'
    assert git('rev-parse','develop')==git('rev-parse','origin/develop')==BASE
    assert git('rev-parse','production')==git('rev-parse','origin/production')==PROD
    assert not git('diff','--name-only','--','migrations','config','universal_supplier/adapters','universal_supplier/postgres.py','universal_supplier/offer_selection.py')
    corpus=json.loads((OUT/'V22_AFTER.json').read_bytes());verification=json.loads((OUT/'OFFLINE_VERIFICATION.json').read_bytes())
    assert corpus['new_auto_accepts']==verification['new_auto_accepts']==145
    assert verification['CSV_raw_roundtrip'] and verification['input_hashes_unchanged']
    assert corpus['removed_auto_accepts']==corpus['changed_candidate_count']==0
    qa=json.loads((OUT/'AI_AUDIT.json').read_bytes())
    assert qa['final_new_auto_accepts_audited']==145 and qa['new_pairs_unreviewed']==0
    assert qa['confirmed_false_positives_found_in_final_saved_evidence_audit']==0
    cases=list(ET.parse(OUT/'REGRESSION_TESTS.xml').getroot().iter('testcase'))
    tests={k:0 for k in ('PASS','FAIL','ERROR','SKIP')}
    for c in cases:
        status=next((k for k,t in [('FAIL','failure'),('ERROR','error'),('SKIP','skipped')] if c.find(t) is not None),'PASS');tests[status]+=1
    assert tests=={'PASS':301,'FAIL':0,'ERROR':0,'SKIP':0}
    save('TEST_RESULTS.json',{**tests,'original_KAMI_calibrations':35,'new_real_positive_fixtures':17,
        'new_real_counterexamples':5,'scope':'Selected offline regression set, not full project suite','DB_tests':0})
    current=set(git('diff','--name-only').splitlines())|set(git('ls-files','--others','--exclude-standard').splitlines())|set(git('diff','--cached','--name-only').splitlines())
    assert current==set(ALLOWED),'Unexpected feature paths: '+','.join(sorted(current^set(ALLOWED)))
    private=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'
    values=json.loads((private/'runtime.json').read_bytes())
    secrets=[str(v) for k,v in values.items() if any(w in k.upper() for w in ('PASSWORD','TOKEN','SECRET')) and v]
    secrets.append((private/'pgpass.conf').read_text().strip().split(':',4)[4])
    scan=[]
    paths=[ROOT/name for name in ALLOWED]+list(OUT.glob('*.csv'))
    for path in paths:
        body=path.read_bytes();text=body.decode('utf-8-sig')
        assert not any(secret and secret in text for secret in secrets),'Known secret (value suppressed): '+path.name
        assert not re.search(r'https?://[^/\s]+/rest/\d+/[A-Za-z0-9]{12,}/|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',text),'Secret-pattern match: '+path.name
        scan.append({'path':str(path.relative_to(ROOT)),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
    save('SECRET_SCOPE_AUDIT.json',{'SECRETS_FOUND':0,'changed_files':ALLOWED,'bulk_raw_snapshots_in_Git':False,
        'CSV_outputs_scanned':6,'application_writers_migrations_adapters_selection_unchanged':True,'files':scan})
    routes=json.loads((OUT/'TERMINAL_ROUTES_ADVISORY.json').read_bytes())['counts']
    total_before=sum(r['Existing BEFORE'] for r in corpus['summary'].values());total_after=sum(r['Existing AFTER'] for r in corpus['summary'].values())
    assert (total_before,total_after)==(1246,1391)
    lines=['| Supplier | Total | Existing BEFORE | Existing AFTER | +Matches | Review AFTER | Conflict AFTER |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for s,r in corpus['summary'].items():lines.append('| '+s+' | '+' | '.join(str(r.get(k,0)) for k in ('Total','Existing BEFORE','Existing AFTER','+Matches','Review AFTER','Conflict AFTER'))+' |')
    report='''# Matching V2.2 AI-assisted saved-evidence rule mining — 2026-10-09

Status: MATCHING_V2_2_AI_RULE_MINING_VERIFIED (feature/isolated dry-run only).
DONE: six-namespace frozen inventory, reason cohorts, native AI semantic review,
deterministic rule mining, raw CSV exports, per-rule ablation, full offline replay.
APPLIED: isolated feature code/tests/docs only. No operational matches written.
VERIFIED:145 incremental dry-run Existing;301 selected offline regressions PASS,
35 old operator calibrations preserved; repeatable9671row evaluation and CSV raw
roundtrip/hashes; saved-evidence final audit145pairs, confirmed false positives0.
This is not independent human ground truth or a universal/live FP-rate guarantee.

## Baselines and authority

Actual develop at start: c0135eef1ad1a2bd502fdc1ade62887974e7c222, V2.1 present.
Worktree/branch are separate from KAMI and Vekprom. Source data read only from
idle approved RC55451/universal_supplier_kami_rc/PG17.11/system7693098812512405428;
active55452 and Vekprom runner/PID/STOP/checkpoint/rows/offer/worktree never accessed.
Source totals9671. Full59500public saved registry SHA40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65
is authoritative offline; physical RC59427 projection is not absence proof.
Frozen raw SQL/properties preserve values and source/canonical provenance.

Stored operational accepted count834 (KAMI717 + Partner117) is not rewritten.
BEFORE1246 is common V2.1 offline evaluation plus preservation of approved existing
links (validated exact FK/current match and active canonical).107 Partner links
remain preserved by their prior namespace-article authority, not counted as a new
V2.2 rule. 412 proposed matches versus stored834 are V2.1 scope reuse, NOT V2.2 lift.
Actual V2.2 incremental lift is145. All results remain proposals, not APPLIED links.
Earlier accepted two FULL NEW/readiness, old506advisory decisions and SQL state are
not silently replaced. This task establishes no new absence/readiness/create result.

## BEFORE → AFTER (offline proposal comparison)

'''+ '\n'.join(lines)+'''

Overall9671:1246→1391Existing (+145),8273Review,7retained Conflict,0new proven NEW.
Auto-match rate12.884%→14.383%. Removed automatic accepts0; accepted target changes0.
Persisted source/offer/selection state is not changed by these proposal counts.

## Actual cohorts and safe patterns

Before complete-model index had8216 no-exact-candidate blockers (not absence proof),
262 kind blockers,46typed contradictions,4 duplicate identities, visible execution/
own-model/axis/condition blockers. Blocker counts overlap; no mechanical summation.
Observed parser errors: complete machining centre classified as tool; generic
"ленточный станок" versus bandsaw; model-only factory H1 with explicit Band saw
category. Raw characteristic pairs from passive source_content were included,
not omitted merely because normalized SQL detail tables lacked those fields.

P1 explicit complete-equipment head, with primary component/embedded controller
separation:105additional matches. P2 explicit own bandsaw category refinement:
40additional after P1. Combined145; all four affected suppliers represented.
No fuzzy/opaque AI acceptance, new aliases/transliteration exceptions, suffix
stripping or product-specific external_id→target implementation.
V2.1 optional trusted kind_resolver defaults to old behavior. Operational runners
remain on current contract; V2.2 is explicit standalone index/dry-run, not deployed.

New accepts rerun exact full execution/brand/visible own model, kind, active unique
canonical, plus/condition/axes and typed critical guards. New automation role/category,
frame/orientation and differing unproven blade-vector guards prevent convenient
field selection. Missing specs/count differences do not impose a positive quota.

Native Codex inspected150 initial candidate name/model pairs (all, not a sample).
Four were withheld due automation/category contradictions; one more source twin-
column versus canonical pendulum frame. These are target quality/identity holds,
not automatic source Conflict or NEW. Final145 all audited, FP found0 within this
saved-evidence audit.17 actual stratified positives +5 actual counterexamples frozen.
Regression safeguards include known LX20Pro/NEW,PP13DNEW/base,ETMcontroller/handle,
HCVjaws/vise,TU2304V/base,45/500S+/base,50/650S+/base and material power contradictions.

Rejected brand-literal pilot: Partner MD-20 complete-machine strong singleton plus
air filter FOR that machine counterexample. No general automatic rule accepted;
singleton manual only, no product-ID hack. AI review152cases includes that pilot.
No actual supplier or Sterbrust HTTP was necessary; public saved evidence reused.

## Residuals (mutually exclusive routing, not a 100% matching target)

'''+ '\n'.join(f'- {k}: {v}' for k,v in routes.items())+'''
- LIKELY_NEW_TO_STERBRUST:0 independently proven in this scope; no exact match is
  not canonical absence. Current old NEW_CANDIDATE SQL labels are not new proof.

Residual totals8280include7retained Conflict; matching classification is separate
from next-evidence route. Brand-literal singleton changes route only, not Review.
Manual ledger10; duplicate canonical groups4 remain fail-closed. Accessories/tool
and source-field gaps are not forced into machine matches. Possible future patterns
require targeted source/canonical/manufacturer proof, not a new mass crawl.

## Scope / QA / handoff

DB writes=0 (including55452), supplierHTTP=0, SterbrustHTTP/writes=0, ESOL=0,
offer selection apply=0, source activation=0, scheduler/deployment=0.
Input hashes stable. Six CSV files UTF8BOM/RFC4180, string/raw identity preserved;
Artifact Tool validates literal values; native serialization used because public
tool docs expose CSV import but no CSV export. No extra XLSX or truncation.
301PASS/0FAIL/0ERROR/0SKIP selected offline tests; full project suite not claimed.
Protected develop/production refs unchanged; feature-only commit/push evidence
recorded separately after operation. Large snapshots/reports/credentials stay ignored.
Resume from CHECKPOINT.json; after feature push STOP. No develop adoption/write
without separate operator permission. Durable workflow:docs/MATCHING_POLICY_V22_AI_REVIEW.md.
'''
    (OUT/'REPORT.md').write_text(report,encoding='utf-8')
    save('CHECKPOINT.json',{'status':'MATCHING_V2_2_AI_RULE_MINING_VERIFIED_PENDING_FEATURE_COMMIT_PUSH',
        'branch':git('branch','--show-current'),'starting_develop_SHA':BASE,'HEAD':git('rev-parse','HEAD'),
        'dry_run_existing_before':1246,'dry_run_existing_after':1391,'new_auto_accepts':145,'Review':8273,'Conflict':7,'new_proven_NEW':0,
        'AI_final_audit_count':145,'confirmed_FP_in_saved_audit':0,'tests':tests,'original_calibrations':35,
        'removed_auto_accepts':0,'changed_accepted_targets':0,'DB_writes':0,'HTTP':0,'active_55452_access':0,'Vekprom_manipulation':0,
        'changed_files':ALLOWED,'manual_high_confidence':10,'residual_routes':routes,
        'resume_from':'Feature allowlisted commit/push only then record exact SHA and STOP; do not repeat data export/analysis/replay',
        'files_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='CHECKPOINT.json'}})
    print('Evidence/allowlist/secret gates PASS, feature commit/push pending')

if __name__=='__main__':main()
