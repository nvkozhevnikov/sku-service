"""Authorized hotspot36 CAS, then unchanged verified RUN2 session/SQL guard."""
from scripts.final_hotspot36 import *
from scripts import final_hotspot36_seals as seals
from scripts import final_hotspot36_metadata_correction as correction
from scripts.verify_inverse97_resume import guarded_repository,protected as previous_protected,same
from dataclasses import asdict
import psycopg,xml.etree.ElementTree as ET

def protected(snapshot):
    result=previous_protected(snapshot);pairs={p['id']:(p,v) for p,v in snapshot['product_offer_projections']}
    for a in r.load(T/'DISK_ALIAS_10_CORRECTION_RECEIPT.json')['rows']:
        pair=pairs[a['source_product_id']];assert r.encoded(pair)==r.encoded((a['verified_source_product'],a['verified_offer']))
        result['rows'].append(dict(cohort='DISK10',source_product_id=a['source_product_id'],pair=pair))
    result['disk10']=10;assert len(result['rows'])==242;return result

def history():
    historical();seals.verify(baseline_seal=seals.ACCEPTED,proposed_transition=seals.PROPOSED)

def prepare():
    history();assert not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists() and not (S/'OFFLINE_PREPARATION.json').exists()
    impact=r.load(S/'FULL_IMPACT.json');assert impact['status']=='PASS' and (impact['changed_persisted'],impact['changed_terminal'])==(36,0)
    tree=ET.parse(S/'TESTS.xml');cases=tree.findall('.//testcase');tests=dict(total=len(cases),passed=len(cases),fail=len(tree.findall('.//failure')),error=len(tree.findall('.//error')),skip=len(tree.findall('.//skipped')))
    assert tests['total']>2971 and tests['fail']==tests['error']==tests['skip']==0;r.save(S/'TEST_RECEIPT.json',tests)
    files={n:r.digest((o.ROOT/n).read_bytes()) for n in r.load(S/'ACCEPTED_ALL_FILE_SEAL.json')}
    for n in ['scripts/final_hotspot36.py','scripts/final_hotspot36_seals.py','scripts/final_hotspot36_metadata_correction.py','scripts/execute_final_hotspot36.py','scripts/qa_final_hotspot36.py','scripts/qa_final_hotspot36_cohorts.py','scripts/closeout_final_hotspot36.py','scripts/verify_inverse97_resume.py','tests/test_final_hotspot36.py','tests/test_final_hotspot36_correction.py','tests/test_inverse97_verify_resume.py']:
      files[n]=r.digest((o.ROOT/n).read_bytes())
    artifacts={n:r.digest((S/n).read_bytes()) for n in ['FULL_IMPACT.json','FINAL_HOTSPOT36_MANIFEST.json','EFFECTIVE_ROW_PLAN.json','FINAL_HOTSPOT_36_CODE_SEAL.json','CODE_PROVENANCE.json','AUTHORIZED_36_ROWS.json','TESTS.xml']}
    correction.validate_manifest(r.load(S/'FINAL_HOTSPOT36_MANIFEST.json'))
    r.save(S/'OFFLINE_PREPARATION.json',dict(status='PASS',files=files,artifacts=artifacts,prior_revised_baseline_sha256=r.digest((T/'DISK_ALIAS_REVISED_BASELINE.json').read_bytes()),tests=tests,database_started=False,checked_at=r.now()))
    print('HOTSPOT36 PRE-DB GATE PASS',tests,flush=True)

def apply():
    history();correction.validate_gate();assert not (S/'BEFORE_CORRECTION.json').exists(),'Inspect durable state; never repeat correction'
    with o.offline_guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as c:
        c.execute("SET application_name='ltt_uss_hotspot36_authorized'");c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        system=r.verify_system();before=o.snapshot(c);same(before,r.load(T/'DISK_ALIAS_REVISED_BASELINE.json'));o.clients(c)
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        r.save(S/'BEFORE_CORRECTION.json',dict(**before,**system,checked_at=r.now()));r.save(S/'PREVIOUS_PROTECTED_BEFORE.json',protected(before))
        manifest=r.load(S/'FINAL_HOTSPOT36_MANIFEST.json')
        receipt=correction.apply(c,manifest,rollback_path=S/'ROLLBACK_BEFORE_COMMIT.json');assert receipt['committed'] and receipt['changed_rows']==36
        r.save(S/'HOTSPOT36_CORRECTION_RECEIPT.json',receipt)
        second=correction.apply(c,manifest,rollback_path=S/'ROLLBACK_BEFORE_COMMIT.json',verify_only=True);assert second['changed_rows']==0;r.save(S/'IDEMPOTENCY_RECEIPT.json',second)
        after=o.snapshot(c);expected=copy.deepcopy(before['product_offer_projections']);targets={a['source_product_id']:a for a in manifest['rows']}
        for pair in expected:
          if pair[0]['id'] in targets:
            for row in pair:row['raw_data']['enrichment_evidence']['product_kind']=copy.deepcopy(targets[pair[0]['id']]['new_kind'])
        assert r.encoded(expected)==r.encoded(after['product_offer_projections']) and after['totals']==before['totals']
        assert {n for n in before['fingerprints'] if before['fingerprints'][n]!=after['fingerprints'][n]}=={'source_products','offers'}
        assert r.encoded(before['sequences'])==r.encoded(after['sequences'])
        assert r.encoded(before['special_products'])==r.encoded(after['special_products']) and r.encoded(before['sib_units'])==r.encoded(after['sib_units'])
        pairs={p['id']:(p,v) for p,v in after['product_offer_projections']}
        receipt.update(rows=[dict(**a,verified_source_product=pairs[a['source_product_id']][0],verified_offer=pairs[a['source_product_id']][1]) for a in manifest['rows']],fresh_committed_pairs_verified=36,other49_tables_unchanged=True,all40_sequences_unchanged=True,idempotent_second_execution_changed_rows=0)
        r.save(S/'HOTSPOT36_CORRECTION_RECEIPT.json',receipt);r.save(S/'PREVIOUS_PROTECTED_AFTER.json',protected(after))
        r.save(S/'HOTSPOT36_REVISED_BASELINE.json',dict(**after,**system,status='HOTSPOT36_CORRECTED_REVISED_BASELINE',checked_at=r.now()))
        r.save(S/'CORRECTION_CLOSEOUT.json',dict(status='PASS',clients=o.clients(c),network=network,prior29_repeated=False,inverse97_repeated=False,checked_at=r.now()))
    print('HOTSPOT36 COMMITTED+FRESH SQL10 /IDEMPOTENCY0 /protected97/29/106 unchanged',flush=True)

def run2():
    history();correction.validate_gate();assert not (S/'RUN2/BEFORE.json').exists(),'Never silently repeat RUN2'
    baseline=r.load(S/'HOTSPOT36_REVISED_BASELINE.json');checked=noops=0;last=None
    with o.offline_guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute("SET application_name='ltt_uss_hotspot36_verify_only'");raw.execute("SET statement_timeout='30s'");raw.execute("SET lock_timeout='5s'")
        # Reuse the verified normal-session factory and exact same SQL guard.
        repo=guarded_repository(raw);c=repo.connection;system=r.verify_system();before=o.snapshot(c);same(before,baseline)
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        r.save(S/'RUN2/PROTECTED_BEFORE.json',protected(before));o.clients(c);r.save(S/'RUN2/BEFORE.json',dict(**before,**system,checked_at=r.now()));counts={s:Counter() for s in r.SOURCES}
        try:
          for e in r.load(S/'EFFECTIVE_ROW_PLAN.json')['rows']:
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];j=r.load(path);outcome,product,capture=o.extract(j);assert outcome==e['outcome']
            if product is not None:
              r.verify_persisted(repo,product,capture);ids=r.check_observation(repo,product,capture);assert ids==j['db_verification']
              result=repo.persist_commercial_observation(product,capture)
              assert result['exact_noop'] and not result['observation_created'] and not result['projection_updated']
              assert result['capture_id']==ids['capture_id'] and all(v==0 for v in asdict(result['ingest_counts']).values());noops+=1
            counts[e['supplier']][outcome]+=1;checked+=1
            r.save(S/'RUN2/ROWS'/e['supplier']/path.name,dict(entry=e,outcome=outcome,persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE'))
            if checked%200==0:print('DISK RUN2',checked,'/2634 noops',noops,flush=True)
          assert checked==2634 and noops==2453
          after=o.snapshot(c);same(after,baseline);r.save(S/'RUN2/PROTECTED_AFTER.json',protected(after));r.save(S/'RUN2/AFTER.json',after)
          r.save(S/'RUN2/RESULT.json',dict(status='PASS',scope=checked,exact_noop_persisted=noops,sources=counts,terminal_outcome_delta=0,table_fingerprint_delta=0,sequence_delta=0,new_source_products=0,new_offers=0,new_captures=0,new_observations=0,sql_writes=0,clients=o.clients(c),network=network,checked_at=r.now()))
        except BaseException as error:
          r.save(S/'RUN2/BLOCKER.json',dict(checked=checked,noops=noops,last=last,error=f'{type(error).__name__}:{error}'));raise
        finally:
          after=o.snapshot(c);same(after,baseline);r.save(S/'RUN2/FINALLY_STATE.json',dict(baseline_intact=True,clients=o.clients(c),network=network,checked_at=r.now()))
    print('DISK RUN2 PASS2634/2453; start brandnew QA',flush=True)

if __name__=='__main__':
    import sys
    {'prepare':prepare,'apply':apply,'run2':run2}[sys.argv[1]]()
