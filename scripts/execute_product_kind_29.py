"""Gated isolated metadata correction, revised baseline and full offline RUN2."""
from dataclasses import asdict
from collections import Counter
import copy
import xml.etree.ElementTree as E
from scripts.prepare_product_kind_29 import P,PREVIOUS,r,o,q,AUDIT
from scripts import ltt_uss_replay_seals as seals
from scripts import product_kind_metadata_correction as correction
from universal_supplier.commercial_persistence import normalize_availability

def tests(name):
    root=E.parse(P/name).getroot();cases=root.findall('.//testcase')
    result=dict(total=len(cases),passed=sum(not any(c.find(t) is not None for t in ['failure','error','skipped']) for c in cases),
        fail=len(root.findall('.//failure')),error=len(root.findall('.//error')),skip=len(root.findall('.//skipped')),
        file=name,sha256=r.digest((P/name).read_bytes()))
    assert result['total']==result['passed'] and result['fail']==result['error']==result['skip']==0,result
    return result

def prepare(guard_normalization=False,post_commit=False):
    assert (P/'OFFLINE_PREPARATION.json').exists()==guard_normalization
    if guard_normalization:
        diagnostic=r.load(P/'TARGET_GUARD_DIAGNOSTIC.json')
        assert diagnostic['unchanged_after_refusal'] and diagnostic['db_writes']==0
        assert diagnostic['target']==['universal_supplier_ltt_uss','127.0.0.1/32','127.0.0.1',55455,'17.11']
        if post_commit:
            assert r.load(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json')['committed']
            assert r.load(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json')['fresh_committed_pairs_verified']==29
            assert r.load(P/'IDEMPOTENCY_RECEIPT.json')['changed_rows']==0
        else:assert not (P/'ROLLBACK_BEFORE_COMMIT.json').exists()
    receipts=dict(focused_and_provenance=tests('FOCUSED_PROVENANCE_TESTS_POSTCOMMIT_VERIFICATION.xml' if post_commit else 'FOCUSED_PROVENANCE_TESTS.xml'),regression=tests('REGRESSION_TESTS.xml'),
        metadata_operation=tests('METADATA_TESTS_GUARD_NORMALIZATION.xml' if guard_normalization else 'METADATA_TESTS.xml'))
    assert receipts['focused_and_provenance']['passed']==(439 if post_commit else 438) and receipts['regression']['passed']==412
    assert r.load(P/'FULL_REPLAY.json')['status']=='FULL_PARSER_COMMERCIAL_OUTCOME_REPLAY_PASS'
    seals.verify_active_code(baseline_seal=seals.PRE_KIND,proposed_transition=seals.KIND_PROPOSAL)
    for filename in ['tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py']:
        before=(P/'BEFORE_HARNESS'/filename).read_text(encoding='utf8')
        current=(o.ROOT/filename).read_text(encoding='utf8')
        assert before.replace("baseline_seal='CURRENT_ACCEPTED_PRE_BATCH_SEAL', proposed_transition='TERMINAL_BATCH_FIX_PROPOSAL'",
            "baseline_seal='CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL', proposed_transition='PROPOSED_PRODUCT_KIND_CODE_SEAL'")==current,'BUSINESS_TEST_ASSERTIONS_CHANGED'
    old=[]
    for phase in [AUDIT,q.OUT,q.accepted.P,PREVIOUS]:
        seal=r.load(phase/'FINAL_ARTIFACT_SEAL.json')
        for name,sha in seal.items():assert r.digest((phase/name).read_bytes())==sha,(phase,name)
        old.append(dict(phase=str(phase.relative_to(o.ROOT)),files=len(seal),unchanged=True))
    bundle=r.load(P/'HISTORICAL_BUNDLE_RECOVERY.json')
    for name,sha in bundle['output_sha256'].items():assert r.digest((__import__('pathlib').Path(bundle['destination'])/name).read_bytes())==sha
    extra=['scripts/ltt_uss_replay_seals.py','scripts/prepare_product_kind_29.py','scripts/replay_product_kind_29.py',
        'scripts/product_kind_metadata_correction.py','scripts/execute_product_kind_29.py','tests/test_product_kind_batch_fix.py',
        'tests/test_product_kind_checkpoint.py','tests/test_product_kind_metadata_correction.py','tests/test_replay_seal_loader.py',
        'tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py','tests/fixtures/product_kind_batch/decisions.json']
    files={**r.load(P/'BUSINESS_CODE_PIN.json'),**{name:r.digest((o.ROOT/name).read_bytes()) for name in extra}}
    assert all(r.digest((o.ROOT/k).read_bytes())==v for k,v in files.items())
    r.save(P/'PRODUCT_KIND_CODE_SEAL.json',files)
    r.save(P/'TEST_RECEIPT.json',dict(executable_pass=sum(a['passed'] for a in receipts.values()),fail=0,error=0,skip=0,non_gating_historical_environment=0,receipts=receipts))
    r.save(P/'PRODUCT_KIND_BATCH_TRANSITION.json',dict(status='ACCEPTED_TESTS_AND_FULL_REPLAY',OLD='CURRENT_ACCEPTED_PRE_PRODUCT_KIND_SEAL',NEW='PRODUCT_KIND_CODE_SEAL',
        original_run1_seal=seals.named_seal(seals.ORIGINAL),terminal_batch_seal=seals.named_seal(seals.TERMINAL),
        pre_kind_seal=seals.named_seal(seals.PRE_KIND),proposed_business_seal=seals.named_seal(seals.KIND_PROPOSAL),
        accepted_code_seal_sha256=r.digest((P/'PRODUCT_KIND_CODE_SEAL.json').read_bytes()),historical_seals_unchanged=old,
        original_business408_assertions_preserved=True,msk318_regression_added=True,provenance_sha256=seals.CHECKPOINT_PROVENANCE_SHA256))
    # Gate receipt is checked again by the correction tool before every write.
    r.save(P/'OFFLINE_PREPARATION.json',dict(status='OFFLINE_PREPARATION_PASS',files=files,
        manifest_sha256=r.digest((P/'PRODUCT_KIND_29_MANIFEST.json').read_bytes()),
        replay_sha256=r.digest((P/'FULL_REPLAY.json').read_bytes()),plan_sha256=r.digest((P/'EFFECTIVE_ROW_PLAN.json').read_bytes()),
        historical_baseline_sha256=r.digest(q.BASELINE.read_bytes()),db_started=guard_normalization,checked_at=r.now()))
    print('All test/replay/code gates PASS; '+('existing retained PG baseline remains unchanged before retry' if guard_normalization else 'DB remains stopped'),flush=True)

def execute(guard_retry=False,post_commit=False):
    gate=correction.validate_gate();assert r.digest((P/'FULL_REPLAY.json').read_bytes())==gate['replay_sha256']
    assert r.digest((P/'EFFECTIVE_ROW_PLAN.json').read_bytes())==gate['plan_sha256']
    assert r.digest(q.BASELINE.read_bytes())==gate['historical_baseline_sha256']
    assert (P/'BEFORE_CORRECTION.json').exists()==guard_retry,'Inspect durable state before any repeat'
    if guard_retry:
        assert r.load(P/'TARGET_GUARD_DIAGNOSTIC.json')['unchanged_after_refusal']
        assert r.load(P/'INITIAL_GUARD_REFUSAL/BLOCKER.json')['db_correction_applied'] is False
        if post_commit:
            assert r.load(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json')['committed']
            assert r.load(P/'IDEMPOTENCY_RECEIPT.json')['changed_rows']==0
        else:assert not (P/'ROLLBACK_BEFORE_COMMIT.json').exists()
    plan=r.load(P/'EFFECTIVE_ROW_PLAN.json')['rows'];manifest=r.load(P/'PRODUCT_KIND_29_MANIFEST.json')
    _,cfg=r.config();assert (cfg['host'],cfg['port'],cfg['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    applied=post_commit;last=None
    with o.offline_guard() as network:
      repo=r.PostgresRepository(r.PostgresConfig(**cfg));c=repo.connection
      try:
        c.execute("SET application_name='ltt_uss_product_kind29_authorized'");c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        system=r.verify_system();current=o.snapshot(c);baseline=r.load(q.BASELINE)
        before=r.load(P/'BEFORE_CORRECTION.json') if post_commit else current
        assert all(r.encoded(before[k])==r.encoded(baseline[k]) for k in current),'ACCEPTED_DB_BASELINE_DRIFT'
        assert before['totals']==dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533)
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        o.clients(c)
        if post_commit:
            r.save(P/'POSTCOMMIT_RESUME_PREFLIGHT.json',dict(**current,**system,reason='No repeat write; verify committed29 against original baseline plus exact manifest',checked_at=r.now()))
            result=r.load(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json')
        else:
            r.save(P/('BEFORE_CORRECTION_RETRY.json' if guard_retry else 'BEFORE_CORRECTION.json'),dict(**before,**system,checked_at=r.now()))
            result=correction.apply(c,manifest,rollback_path=P/'ROLLBACK_BEFORE_COMMIT.json');applied=result['changed_rows']==29
            r.save(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json',result)
        second=correction.apply(c,manifest,rollback_path=P/'ROLLBACK_BEFORE_COMMIT.json',verify_only=True)
        assert second['changed_rows']==0;r.save(P/'IDEMPOTENCY_RECEIPT.json',second)
        after=o.snapshot(c)
        assert after['totals']==before['totals'] and r.encoded(after['sequences'])==r.encoded(before['sequences'])
        assert {k for k in before['fingerprints'] if before['fingerprints'][k]!=after['fingerprints'][k]}=={'source_products','offers'}
        assert all(before['supplier_namespaces'][s]==after['supplier_namespaces'][s] for s in ['ltt_yug','uss_stanko'])
        expected=copy.deepcopy(before['product_offer_projections']);byid={a['source_product_id']:a for a in manifest['rows']}
        for pair in expected:
            if pair[0]['id'] in byid:
                a=byid[pair[0]['id']]
                for row in pair:row['raw_data']['enrichment_evidence']['product_kind']=copy.deepcopy(a['new_kind'])
        assert r.encoded(expected)==r.encoded(after['product_offer_projections']),'UNAUTHORIZED_PROJECTION_DELTA'
        actual_pairs={p['id']:(p,v) for p,v in after['product_offer_projections']}
        result.update(rows=[dict(**a,verified_source_product=actual_pairs[a['source_product_id']][0],
            verified_offer=actual_pairs[a['source_product_id']][1]) for a in manifest['rows']],
            fresh_committed_pairs_verified=29,protected_49_tables_unchanged=True,all_40_sequences_unchanged=True,
            foreign_yug_uss_namespaces_unchanged=True,idempotent_second_execution_changed_rows=0)
        r.save(P/'PRODUCT_KIND_29_CORRECTION_RECEIPT.json',result)
        assert r.encoded(before['special_products'])==r.encoded(after['special_products']) and r.encoded(before['sib_units'])==r.encoded(after['sib_units'])
        mandatory=c.execute('''SELECT p.external_id,p.id,o.id,o.price,o.currency,o.availability_normalized,
            p.raw_data->'enrichment_evidence'->'product_kind',o.raw_data->'enrichment_evidence'->'product_kind'
            FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
            WHERE s.code='ltt_msk' AND p.external_id IN ('1910','1642','1860') ORDER BY p.external_id''').fetchall()
        assert len(mandatory)==3 and len({x[1] for x in mandatory})==3 and all(x[6]==x[7] for x in mandatory)
        row129=next(x for x in mandatory if x[0]=='1910');assert row129[3:6]==(42000,'RUB',normalize_availability('OutOfStock')) and row129[6]['product_kind']=='fixture'
        assert next(x for x in mandatory if x[0]=='1642')[6]['product_kind']=='saw_blade'
        r.save(P/'MANDATORY_SQL_VERIFICATION.json',dict(rows=mandatory,msk318_msk40_distinct=True,row129_fixed=True))
        r.save(P/'REVISED_BASELINE_AFTER_PRODUCT_KIND_BATCH_FIX.json',dict(**after,**system,status='PRODUCT_KIND29_CORRECTED_BASELINE',manifest='PRODUCT_KIND_29_MANIFEST.json',checked_at=r.now()))
        r.save(P/'RUN2/BEFORE.json',after)
        noop=r.PostgresRepository.__new__(r.PostgresRepository);noop.connection=o.NoMutationConnection(c)
        counts={s:Counter() for s in r.SOURCES};processed=noops=0
        for e in plan:
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256']
            row=r.load(path);outcome,product,capture=o.extract(row);assert outcome==e['outcome']
            if product is not None:
                r.verify_persisted(noop,product,capture);assert r.check_observation(noop,product,capture)==row['db_verification']
                receipt=noop.persist_commercial_observation(product,capture)
                assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
                assert all(x==0 for x in asdict(receipt['ingest_counts']).values());noops+=1
            elif e['supplier']=='uss_stanko' and e['index'] in {191,194,195,196,197}:
                fact=r.load(P/'PARSED_ROWS/uss_stanko'/f"{e['index']:06d}.json")
                meta=row['result'];body=(r.OUT/'EVIDENCE/uss_stanko'/(meta['evidence_sha256']+'.html')).read_bytes()
                parsed=r.parse('uss_stanko',body,meta['final_url']);assert parsed.enrichment_evidence['product_kind']==fact['parsed_product']['enrichment_evidence']['product_kind']
                assert parsed.enrichment_evidence['product_kind']['product_kind']=='cutting_tool'
            counts[e['supplier']][outcome]+=1;processed+=1
            r.save(P/'RUN2/ROWS'/e['supplier']/path.name,dict(supplier=e['supplier'],index=e['index'],outcome=outcome,
                persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE',journal_sha256=e['journal_sha256']))
            if processed%200==0:
                r.save(P/'CHECKPOINT.json',dict(status='PRODUCT_KIND29_RUN2_RUNNING',processed=processed,total=2634,safe_to_power_off=False));print('NEW FULL RUN2',processed,'/2634',flush=True)
        assert processed==2634 and noops==2453
        assert counts['uss_stanko']==Counter(PERSISTED=227,AMBIGUOUS=114,MULTI_VARIANT=66)
        final=o.snapshot(c);assert all(r.encoded(after[k])==r.encoded(final[k]) for k in after),'RUN2_DATABASE_DELTA'
        correction.validate_gate();clients=o.clients(c)
        r.save(P/'RUN2/AFTER.json',final)
        r.save(P/'RUN2/RESULT.json',dict(status='FULL_OFFLINE_RUN2_PASS',scope=2634,exact_noop_persisted=2453,
            new_source_products=0,new_offers=0,new_captures=0,new_observations=0,table_fingerprint_delta=0,sequence_delta=0,
            terminal_outcome_delta=0,sources=counts,clients=clients,network=network,checked_at=r.now()))
        r.save(P/'HTTP_ZERO_PROOF.json',network)
        r.save(P/'RESULT.json',dict(status='LTT_USS_PRODUCT_KIND_BATCH_FIX_AND_RUN2_REVERIFIED',corrected=29,msk=17,sib=12,
            terminal_fixed=5,counts=final['totals'],full_qa_resumed=False,finalizer_started=False,db_correction_applied=True,
            matching_run=False,selection_run=False,supplier_http=0,safe_to_power_off=False,next='CLEAN_SHUTDOWN_REQUIRED'))
        if guard_retry:r.save(P/'GUARD_REFUSAL_RESOLVED.json',dict(status='RESOLVED_ADDRESS_REPRESENTATION_ONLY',
            original='INITIAL_GUARD_REFUSAL/BLOCKER.json',normalized_by='host(inet_server_addr())',guard_not_bypassed=True,
            initial_refusal_db_writes=0,retained_baseline_reverified_before_write=True,correction29_and_run2_pass=True))
        print('DB correction29 + idempotency0 + RUN2 2634/2453 NOOP verified; clean shutdown required',flush=True)
      except BaseException as error:
        r.save(P/'BLOCKER.json',dict(status='LTT_USS_PRODUCT_KIND_BATCH_FIX_BLOCKED',stage='DB_CORRECTION_OR_RUN2',
            error=f'{type(error).__name__}:{error}',last=last,db_started=True,db_correction_applied=applied,network=network,safe_to_power_off=False))
        raise
      finally:repo.close()

if __name__=='__main__':
    import sys
    {'prepare':prepare,'execute':execute,'prepare_guard_normalization':lambda:prepare(True),'execute_guard_retry':lambda:execute(True),
        'prepare_postcommit_verification':lambda:prepare(True,True),'resume_postcommit_verification_run2':lambda:execute(True,True)}[sys.argv[1]]()
