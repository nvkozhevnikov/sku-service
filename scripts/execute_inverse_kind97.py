"""Authorized97 correction then complete SELECT-only RUN2; gated before startup."""
from scripts.replay_inverse_kind97 import B,r,o,q,P,old,AUDIT
from scripts import inverse_kind_metadata_correction as correction
from scripts import inverse_kind_seals as seals
from dataclasses import asdict
from collections import Counter
from universal_supplier.commercial_persistence import normalize_availability
import copy,xml.etree.ElementTree as ET
import psycopg

def historical():
    for phase in [P,old.OUT,AUDIT]:
        for n,sha in r.load(phase/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((phase/n).read_bytes())==sha,('HISTORY_DRIFT',phase,n)

def prepare():
    assert not (B/'OFFLINE_PREPARATION.json').exists() and not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists()
    impact=r.load(B/'FULL_IMPACT.json');assert impact['status']=='PASS' and (impact['changed_persisted'],impact['changed_terminal'])==(97,4)
    root=ET.parse(B/'FINAL_TESTS.xml');cases=root.findall('.//testcase');receipt=dict(total=len(cases),passed=sum(not any(c.find(t) is not None for t in ['failure','error','skipped']) for c in cases),fail=len(root.findall('.//failure')),error=len(root.findall('.//error')),skip=len(root.findall('.//skipped')))
    assert receipt['total']==receipt['passed'] and receipt['fail']==receipt['error']==receipt['skip']==0
    r.save(B/'TEST_RECEIPT.json',receipt);historical();seals.verify(baseline_seal=seals.ACCEPTED,proposed_transition=seals.PROPOSED)
    previous=r.load(B/'PREFLIGHT.json')['accepted_code'];files={n:r.digest((o.ROOT/n).read_bytes()) for n in previous}
    changed={n for n in previous if previous[n]!=files[n]}
    assert changed=={'sterbrust_matching/product_identity.py','universal_supplier/adapters/ltt_uss_html.py','scripts/ltt_uss_replay_seals.py','tests/test_product_kind_checkpoint.py','tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py'},changed
    for n in ['scripts/inverse_kind_seals.py','scripts/replay_inverse_kind97.py','scripts/inverse_kind_metadata_correction.py','scripts/execute_inverse_kind97.py','tests/test_inverse_kind97.py','tests/test_inverse_kind97_seals.py','tests/test_inverse_kind97_correction.py','tests/test_uss_terminal_batch.py']:files[n]=r.digest((o.ROOT/n).read_bytes())
    artifacts={n:r.digest((B/n).read_bytes()) for n in ['FULL_IMPACT.json','FINAL_TESTS.xml','TEST_RECEIPT.json','INVERSE_KIND_97_MANIFEST.json','EFFECTIVE_ROW_PLAN.json','CHECKPOINT_PROVENANCE.json','AUTHORIZED_97_ROWS.json','AUTHORIZED_TERMINAL4.json']}
    correction.validate_manifest(r.load(B/'INVERSE_KIND_97_MANIFEST.json'))
    r.save(B/'INVERSE_KIND_97_CODE_SEAL.json',files)
    r.save(B/'INVERSE_KIND_CODE_TRANSITION.json',dict(status='ACCEPTED_CODE_TESTS_FULL_IMPACT',OLD=seals.ACCEPTED,NEW='INVERSE_KIND_97_CODE_SEAL',proposed_business_archive=seals.PROPOSED,provenance_sha256=seals.SHA,changed_business_paths=seals.provenance()['approved_changed_paths'],historical_seals_preserved=True,exact_persisted97=True,exact_terminal4=True))
    r.save(B/'OFFLINE_PREPARATION.json',dict(status='PASS',files=files,artifacts=artifacts,old_baseline_sha256=r.digest(old.BASELINE.read_bytes()),database_started=False,checked_at=r.now()))
    print('PRE-DB ALL GATES PASS',receipt,flush=True)

def execute():
    correction.validate_gate();assert not (B/'BEFORE_CORRECTION.json').exists(),'Inspect durable correction state before repeating'
    historical();manifest=r.load(B/'INVERSE_KIND_97_MANIFEST.json');baseline=r.load(old.BASELINE)
    assert r.digest(old.BASELINE.read_bytes())==r.load(B/'OFFLINE_PREPARATION.json')['old_baseline_sha256']
    applied=False;last=None
    with o.offline_guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as c:
       try:
        c.execute("SET application_name='ltt_uss_inverse97_authorized'");c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        system=r.verify_system();before=o.snapshot(c);assert all(r.encoded(before[k])==r.encoded(baseline[k]) for k in before),'ACCEPTED_BASELINE_DRIFT'
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40;o.clients(c)
        r.save(B/'BEFORE_CORRECTION.json',dict(**before,**system,checked_at=r.now()))
        receipt=correction.apply(c,manifest,rollback_path=B/'ROLLBACK_BEFORE_COMMIT.json');applied=receipt['committed'];r.save(B/'INVERSE_KIND_97_CORRECTION_RECEIPT.json',receipt)
        second=correction.apply(c,manifest,rollback_path=B/'ROLLBACK_BEFORE_COMMIT.json',verify_only=True);assert second['changed_rows']==0;r.save(B/'IDEMPOTENCY_RECEIPT.json',second)
        after=o.snapshot(c);assert after['totals']==before['totals'] and r.encoded(after['sequences'])==r.encoded(before['sequences'])
        assert {n for n in before['fingerprints'] if before['fingerprints'][n]!=after['fingerprints'][n]}=={'source_products','offers'}
        expected=copy.deepcopy(before['product_offer_projections']);changes={a['source_product_id']:a for a in manifest['rows']}
        for pair in expected:
            if pair[0]['id'] in changes:
                for row in pair:row['raw_data']['enrichment_evidence']['product_kind']=copy.deepcopy(changes[pair[0]['id']]['new_kind'])
        assert r.encoded(expected)==r.encoded(after['product_offer_projections']),'UNAUTHORIZED_PROJECTION_DELTA'
        actual={p['id']:(p,v) for p,v in after['product_offer_projections']};oldpairs={p['id']:(p,v) for p,v in before['product_offer_projections']}
        receipt.update(fresh_committed_pairs_verified=97,rows=[dict(**a,verified_source_product=actual[a['source_product_id']][0],verified_offer=actual[a['source_product_id']][1]) for a in manifest['rows']],protected49_tables_unchanged=True,all40_sequences_unchanged=True,idempotent_second_execution_changed_rows=0)
        r.save(B/'INVERSE_KIND_97_CORRECTION_RECEIPT.json',receipt)
        protected=[]
        cohorts={'PREVIOUS29':r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows'],'OPERATOR106':[a for a in r.load(AUDIT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='OPERATOR_REVIEW_REQUIRED']}
        for label,rows in cohorts.items():
            for a in rows:
                pid=a.get('source_product_id') or a['db_evidence']['source_product_id'];assert r.encoded(actual[pid])==r.encoded(oldpairs[pid]);protected.append(dict(cohort=label,supplier=a['supplier'],index=a['index'],source_product_id=pid,status='UNCHANGED_FRESH_SQL_PAIR'))
        assert len(protected)==135;r.save(B/'PROTECTED_29_AND_106_FRESH_SQL.json',dict(previous29_unchanged=29,operator106_unchanged=106,rows=protected))
        row82=actual[142];assert row82[0]['external_id']=='1745' and row82[1]['price']==319000 and row82[1]['currency']=='RUB' and row82[1]['availability_normalized']==normalize_availability('InStock')
        assert row82[0]['raw_data']['enrichment_evidence']['product_kind']==row82[1]['raw_data']['enrichment_evidence']['product_kind'] and row82[0]['raw_data']['enrichment_evidence']['product_kind']['product_kind']=='machine'
        r.save(B/'ROW82_FRESH_SQL.json',dict(source_product=row82[0],offer=row82[1],status='PASS'))
        assert r.encoded(before['special_products'])==r.encoded(after['special_products']) and r.encoded(before['sib_units'])==r.encoded(after['sib_units'])
        r.save(B/'INVERSE_KIND_REVISED_BASELINE.json',dict(**after,**system,status='INVERSE97_CORRECTED_BASELINE',checked_at=r.now()))
        c.execute('SET default_transaction_read_only=on');assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        repo=r.PostgresRepository.__new__(r.PostgresRepository);repo.connection=o.NoMutationConnection(c)
        r.save(B/'RUN2/BEFORE.json',after);counts={s:Counter() for s in r.SOURCES};noops=0
        plan=r.load(B/'EFFECTIVE_ROW_PLAN.json')['rows']
        for n,e in enumerate(plan,1):
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];j=r.load(path);outcome,product,capture=o.extract(j);assert outcome==e['outcome']
            if product is not None:
                r.verify_persisted(repo,product,capture);assert r.check_observation(repo,product,capture)==j['db_verification']
                result=repo.persist_commercial_observation(product,capture);assert result['exact_noop'] and not result['observation_created'] and not result['projection_updated'] and all(x==0 for x in asdict(result['ingest_counts']).values());noops+=1
            counts[e['supplier']][outcome]+=1;r.save(B/'RUN2/ROWS'/e['supplier']/path.name,dict(entry=e,outcome=outcome,persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE'))
            if n%200==0:print('INVERSE NEW RUN2',n,'/2634',flush=True)
        final=o.snapshot(c);assert noops==2453 and all(r.encoded(after[k])==r.encoded(final[k]) for k in after),'RUN2_STATE_DRIFT'
        clients=o.clients(c);r.save(B/'RUN2/AFTER.json',final);r.save(B/'RUN2/RESULT.json',dict(status='PASS',scope=2634,exact_noop_persisted=2453,sources=counts,terminal_outcome_delta=0,table_fingerprint_delta=0,sequence_delta=0,new_source_products=0,new_offers=0,new_captures=0,new_observations=0,clients=clients,network=network,checked_at=r.now()))
        print('CORRECTION97 / IDEMPOTENCY0 / NEW RUN2 2634/2453 PASS; start fresh QA next',flush=True)
       except BaseException as error:
        r.save(B/'BLOCKER.json',dict(status='LTT_USS_SOURCE_INTEGRATION_FINAL_BLOCKED',stage='CORRECTION_OR_RUN2',error=f'{type(error).__name__}:{error}',db_correction_applied=applied,last=last,SAFE_TO_POWER_OFF='PENDING'));raise

if __name__=='__main__':
    import sys
    {'prepare':prepare,'execute':execute}[sys.argv[1]]()
