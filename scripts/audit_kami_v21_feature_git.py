"""Allowlisted feature diff, compact receipts, and secret scan. No DB writer."""
import hashlib,json,os,re,subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from scripts.package_kami_v21_checkpoint import ROOT,OUT,PREV,PRIVATE

ALLOWED=['.gitignore','ACCEPTANCE_MATRIX.md','WORKING_RULES.md','QUALITY_GATES.md','ANTIPATTERNS.md',
    'FALSE_MATCH_CASES.md','CURRENT_STATE.md','docs/FINAL_RC_CURRENT_STATE.md','docs/MATCHING_RULES.md',
    'docs/KAMI_V21_FEATURE_CHECKPOINT.md','docs/evidence/kami_v21_checkpoint_20261008.json',
    'universal_supplier/matching_policy_v2.py','tests/test_matching_policy_v2.py','tests/fixtures/kami_v21_operator_calibrations.json',
    'scripts/apply_kami_policy_v2_1.py','scripts/audit_kami_policy_v2_1.py','scripts/finalize_kami_policy_v2_1.py',
    'scripts/fixtures_kami_policy_v2_1.py','scripts/propose_kami_policy_v2.py','scripts/qa_kami_policy_v2_1.py',
    'scripts/freeze_kami_v21_fixtures.py','scripts/package_kami_v21_checkpoint.py','scripts/audit_kami_v21_feature_git.py']
LOCAL_ONLY=['scripts/checkpoint_kami_policy_v2_stop.py','scripts/verify_kami_policy_v2_state.py','scripts/verify_kami_policy_v2_stop_artifacts.py']


def save(name,v):(OUT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf8')


def main():
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
    assert git('branch','--show-current')=='feature/universal-supplier-kami-rc1'
    suites=ET.parse(OUT/'FOCUSED_TESTS.xml').getroot().findall('testsuite')
    tests={k:sum(int(s.get(k,'0')) for s in suites) for k in ('tests','failures','errors','skipped')}
    assert tests=={'tests':233,'failures':0,'errors':0,'skipped':0}
    tests.update(PASS=233,FAIL=0,ERROR=0,SKIP=0,positive_calibrations=35,
        portable_positive_fixture_bytes=(ROOT/'tests/fixtures/kami_v21_operator_calibrations.json').stat().st_size,
        scratch_database='universal_supplier_kami_lifecycle_test_20261006',RC_business_writes=0,full_suite='NOT_RUN_OUTSIDE_SCOPE')
    save('TEST_RESULTS.json',tests)
    dump=json.loads((OUT/'FINAL_DUMP.json').read_bytes());restore=json.loads((OUT/'RESTORE_VERIFICATION.json').read_bytes())
    assert restore['source_unchanged'] and restore['public_tables_sequences_exact_equal']
    assert json.loads((OUT/'DB_BEFORE_FINGERPRINTS.json').read_bytes())==json.loads((OUT/'DB_AFTER_FINGERPRINTS.json').read_bytes())
    identity=json.loads((OUT/'MATCHING_IDENTITY_HASHES.json').read_bytes())
    compact={'provenance':'LIVE-VERIFIED isolated QA/dev; OPERATOR-CONFIRMED 2026-10-08 bf5d1d67',
        'matching':{'source_identities':5264,'offers':5264,'Existing':717,'Review':4547,'Conflict':0,
            'matched_numeric_public':90,'exact_numeric':88,'FROM_PRICE':2,'supplier_disabled':True,'offers_inactive':True},
        'selection':{'rows':121,'selected':117,'no_eligible_offer':4,'changes_in_finalization':0},
        'accepted_identity_sha256':identity['accepted_identity_sha256'],'tests':tests,
        'dump':{k:dump[k] for k in ('bytes','sha256','modified_at','database','PG_version','system_identifier','format')},
        'dump_filename':Path(dump['path']).name,'restore':{'status':restore['status'],'database':restore['database'],'source_unchanged':True},
        'migration_names':sorted(dump['migrations']),'input_hashes':json.loads((PREV/'V2_1_PROPOSAL_DATA.json').read_bytes())['summary']['input_hashes'],
        'source_business_writes':0,'supplier_HTTP':0,'Sterbrust_writes':0,'ESOL':0,'selection_changes':0,
        'Git_commit_push_evidence':'recorded after operation in external finalization GIT_FINAL_STATE.json; not self-referential',
        'not_authorized':['develop/production/tag','selection evaluation/apply','import/XML','NEW creation','deployment/scheduler']}
    target=ROOT/'docs/evidence/kami_v21_checkpoint_20261008.json';target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf8')
    # Pending local-only helpers remain at their exact paths/hashes: no reset,
    # delete or rewrite of prior work; their outputs are historical ignored data.
    runtime=json.loads((PRIVATE/'runtime.json').read_bytes())
    secrets=[str(v) for k,v in runtime.items() if any(w in k.upper() for w in ('PASSWORD','SECRET','TOKEN')) and v]
    secrets.append((PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4])
    current=set(git('diff','--name-only').splitlines())|set(git('ls-files','--others','--exclude-standard').splitlines())|set(git('diff','--cached','--name-only').splitlines())
    unexpected=current-set(ALLOWED)
    assert not unexpected,'Unexplained feature paths: '+','.join(sorted(unexpected))
    scan=[]
    for name in ALLOWED:
        path=ROOT/name;assert path.is_file(),name
        raw=path.read_bytes();text=raw.decode('utf8')
        assert not any(s and s in text for s in secrets),'Known secret exposure (value suppressed): '+name
        assert not re.search(r'https?://[^/\s]+/rest/\d+/[a-zA-Z0-9]{12,}/',text),'Credential-bearing webhook URL: '+name
        assert not re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}',text),'Private credential pattern: '+name
        assert len(raw)<2_000_000,'Unexplained bulk file: '+name
        scan.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'secret_scan':'PASS'})
    assert not git('ls-files','release/database','reports','*.dump','*.pgpass','.env')
    audit={'status':'FEATURE_ALLOWLIST_SECRET_DIFF_PASS','files':scan,'categories':{
        'application':['universal_supplier/matching_policy_v2.py'],
        'tests':['tests/test_matching_policy_v2.py','tests/fixtures/kami_v21_operator_calibrations.json'],
        'operator_workflow_scripts':[f for f in ALLOWED if f.startswith('scripts/')],
        'docs_metadata':[f for f in ALLOWED if f.endswith('.md') or f.startswith('docs/evidence/')],
        'reports_dumps_raw_evidence':'EXTERNAL_GITIGNORED_NOT_STAGED'},
        'local_historical_helpers_preserved_excluded':[{'path':f,'sha256':hashlib.sha256((ROOT/f).read_bytes()).hexdigest()} for f in LOCAL_ONLY],
        'unexpected_unrelated_paths':[],'known_secret_scan':'PASS','pattern_secret_scan':'PASS','generated_bulk_staged':False,
        'starting_HEAD':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),
        'protected_refs':{n:git('rev-parse',n) for n in ('develop','origin/develop','production','origin/production')},
        'workspace_knowledge_docs_external_to_feature_Git':['CURRENT_STATE.md','ACCEPTANCE_MATRIX.md','ANTIPATTERNS.md','FALSE_MATCH_CASES.md','context/WORKING_RULES.md','qa/QUALITY_GATES.md','tasks/active/2026-10-05_UNIVERSAL_SUPPLIER_KAMI_RC.md']}
    save('GIT_DIFF_AUDIT.json',audit)
    print(json.dumps({'status':audit['status'],'files':len(scan),'bytes':sum(f['bytes'] for f in scan),'tests':tests,'dump_sha256':dump['sha256']}))


if __name__=='__main__':main()
