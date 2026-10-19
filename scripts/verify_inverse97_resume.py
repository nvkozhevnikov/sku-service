"""Verify already committed inverse97; never imports a correction executor."""
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o
from scripts import inverse_kind_seals as seals
from collections import Counter
from dataclasses import asdict
import xml.etree.ElementTree as ET
import psycopg

B=r.BASE/'INVERSE_KIND_97_BATCH_2026-10-16'
N=r.BASE/'INVERSE97_VERIFY_ONLY_RESUME_2026-10-16'
P=r.BASE/'PRODUCT_KIND_29_RESUME_2026-10-16'
AUDIT=r.BASE/'INVERSE_KIND_IMPACT_SWEEP_2026-10-16'

def history():
    for name,sha in r.load(B/'FINAL_ARTIFACT_SEAL.json').items():
        assert r.digest((B/name).read_bytes())==sha,('APPLIED_EVIDENCE_DRIFT',name)
    for name,sha in r.load(B/'INVERSE_KIND_97_CODE_SEAL.json').items():
        assert r.digest((o.ROOT/name).read_bytes())==sha,('APPLIED_CODE_DRIFT',name)
    seals.verify(baseline_seal=seals.ACCEPTED,proposed_transition=seals.PROPOSED)
    receipt=r.load(B/'INVERSE_KIND_97_CORRECTION_RECEIPT.json')
    assert receipt['committed'] and receipt['fresh_committed_pairs_verified']==97
    assert r.load(B/'IDEMPOTENCY_RECEIPT.json')['changed_rows']==0
    assert r.load(B/'FINAL_CHECKPOINT.json')['SAFE_TO_POWER_OFF']=='YES'
    plan=r.load(B/'EFFECTIVE_ROW_PLAN.json')['rows'];assert len(plan)==2634
    for e in plan:assert r.digest((o.ROOT/e['journal']).read_bytes())==e['journal_sha256']

def guarded_repository(raw):
    # Normal session is required for accepted repository SELECT FOR UPDATE.
    # Never change PostgreSQL read-only settings here: reject unexpected defaults.
    assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='off'
    assert raw.execute('SHOW default_transaction_read_only').fetchone()[0]=='off'
    repo=r.PostgresRepository.__new__(r.PostgresRepository)
    repo.connection=o.NoMutationConnection(raw)
    return repo

def preflight():
    history();assert not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists()
    tree=ET.parse(N/'TESTS.xml');cases=tree.findall('.//testcase')
    tests=dict(total=len(cases),passed=len(cases),fail=len(tree.findall('.//failure')),error=len(tree.findall('.//error')),skip=len(tree.findall('.//skipped')))
    assert len(cases)>2076 and tests['fail']==tests['error']==tests['skip']==0
    r.save(N/'TEST_RECEIPT.json',tests)
    files={n:r.digest((o.ROOT/n).read_bytes()) for n in ['scripts/verify_inverse97_resume.py','tests/test_inverse97_verify_resume.py','scripts/qa_inverse97_verify_resume.py']}
    r.save(N/'OFFLINE_PREFLIGHT.json',dict(status='PASS',tests=tests,harness_files=files,correction_entrypoint_imported=False,correction97_repeated=False,baseline_sha256=r.digest((B/'INVERSE_KIND_REVISED_BASELINE.json').read_bytes()),original_artifact_seal_sha256=r.digest((B/'FINAL_ARTIFACT_SEAL.json').read_bytes()),checked_at=r.now()))
    print('VERIFY-ONLY PREFLIGHT PASS',tests,flush=True)

def same(current,baseline):
    assert not [k for k in current if r.encoded(current[k])!=r.encoded(baseline[k])], 'CURRENT_REVISED_BASELINE_DRIFT'

def protected(snapshot):
    pairs={p['id']:(p,v) for p,v in snapshot['product_offer_projections']}
    rows=[]
    for a in r.load(B/'INVERSE_KIND_97_CORRECTION_RECEIPT.json')['rows']:
        actual=pairs[a['source_product_id']]
        assert r.encoded(actual)==r.encoded((a['verified_source_product'],a['verified_offer']))
        rows.append(dict(cohort='INVERSE97',source_product_id=a['source_product_id'],pair=actual))
    protected_rows=r.load(B/'PROTECTED_29_AND_106_FRESH_SQL.json')['rows']
    baseline_pairs={p['id']:(p,v) for p,v in r.load(B/'INVERSE_KIND_REVISED_BASELINE.json')['product_offer_projections']}
    assert Counter(a['cohort'] for a in protected_rows)=={'PREVIOUS29':29,'OPERATOR106':106}
    for a in protected_rows:
        pid=a['source_product_id'];assert r.encoded(pairs[pid])==r.encoded(baseline_pairs[pid])
        rows.append(dict(cohort=a['cohort'],source_product_id=pid,pair=pairs[pid]))
    assert len(rows)==232
    return dict(status='PASS',inverse97=97,previous29=29,operator106=106,rows=rows)

def execute():
    history();gate=r.load(N/'OFFLINE_PREFLIGHT.json');assert gate['status']=='PASS'
    for path,sha in gate['harness_files'].items():assert r.digest((o.ROOT/path).read_bytes())==sha
    assert not (N/'RUN2/BEFORE.json').exists(),'Never silently repeat RUN2'
    baseline=r.load(B/'INVERSE_KIND_REVISED_BASELINE.json');checked=noops=0;last=None
    with o.offline_guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute("SET application_name='ltt_uss_inverse97_verify_only'")
        raw.execute("SET statement_timeout='30s'");raw.execute("SET lock_timeout='5s'")
        repo=guarded_repository(raw);c=repo.connection
        system=r.verify_system();before=o.snapshot(c);same(before,baseline)
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        r.save(N/'RUN2/PROTECTED_BEFORE.json',protected(before));o.clients(c)
        r.save(N/'RUN2/BEFORE.json',dict(**before,**system,checked_at=r.now()))
        counts={s:Counter() for s in r.SOURCES}
        try:
          for e in r.load(B/'EFFECTIVE_ROW_PLAN.json')['rows']:
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256']
            j=r.load(path);outcome,product,capture=o.extract(j);assert outcome==e['outcome']
            if product is not None:
                r.verify_persisted(repo,product,capture);ids=r.check_observation(repo,product,capture);assert ids==j['db_verification']
                result=repo.persist_commercial_observation(product,capture)
                assert result['exact_noop'] and not result['observation_created'] and not result['projection_updated']
                assert result['capture_id']==ids['capture_id'] and all(v==0 for v in asdict(result['ingest_counts']).values())
                noops+=1
            counts[e['supplier']][outcome]+=1;checked+=1
            r.save(N/'RUN2/ROWS'/e['supplier']/path.name,dict(entry=e,outcome=outcome,persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE'))
            if checked%200==0:print('VERIFY-ONLY RUN2',checked,'/2634 noops',noops,flush=True)
          assert checked==2634 and noops==2453
          after=o.snapshot(c);same(after,baseline)
          r.save(N/'RUN2/PROTECTED_AFTER.json',protected(after));r.save(N/'RUN2/AFTER.json',after)
          r.save(N/'RUN2/RESULT.json',dict(status='PASS',scope=checked,exact_noop_persisted=noops,sources=counts,terminal_outcome_delta=0,table_fingerprint_delta=0,sequence_delta=0,new_source_products=0,new_offers=0,new_captures=0,new_observations=0,sql_writes=0,correction97_repeated=False,clients=o.clients(c),network=network,checked_at=r.now()))
        except BaseException as error:
          r.save(N/'RUN2/BLOCKER.json',dict(checked=checked,noops=noops,last=last,error=f'{type(error).__name__}:{error}',correction97_repeated=False));raise
        finally:
          after=o.snapshot(c);same(after,baseline)
          r.save(N/'RUN2/FINALLY_STATE.json',dict(baseline_intact=True,clients=o.clients(c),network=network,checked_at=r.now()))
    print('RUN2 PASS 2634/2634 /2453 EXACT_NOOP',flush=True)

if __name__=='__main__':
    import sys
    {'preflight':preflight,'execute':execute}[sys.argv[1]]()
