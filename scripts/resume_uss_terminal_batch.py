"""Explicit-checkpoint provenance -> gated exact8 backfill -> full offline RUN2."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import copy
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import uss_terminal_batch_fix as b
from scripts import ltt_uss_replay_seals as seals
r,o=b.r,b.o
P=r.BASE/'USS_SEAL_LOADER_RESUME_2026-10-15'
BASELINE_NAME='REVISED_RUN1_BASELINE_AFTER_TERMINAL_BATCH_FIX.json'

def history():
    b.history();seals.provenance()
    for key,sha in r.load(b.PHASE/'FINAL_ARTIFACT_SEAL.json').items():
        assert r.digest((b.PHASE/key).read_bytes())==sha,('OLD_BATCH_EVIDENCE_DRIFT',key)

def test_receipt(name,expected):
    path=P/name;root=ET.parse(path).getroot();cases=root.findall('.//testcase')
    result=dict(total=len(cases),passed=sum(not any(c.find(t) is not None for t in ['failure','error','skipped']) for c in cases),fail=len(root.findall('.//failure')),error=len(root.findall('.//error')),skip=len(root.findall('.//skipped')),xml=name,sha256=r.digest(path.read_bytes()))
    assert result['total']==result['passed']==expected and result['fail']==result['error']==result['skip']==0,result
    return result

def prepare():
    assert not (P/'OFFLINE_PREPARATION.json').exists(),'Do not silently repeat accepted preparation'
    tests=dict(previous9=test_receipt('PREVIOUS_9_TESTS.xml',9),loader=test_receipt('LOADER_TESTS.xml',18),regression=test_receipt('REGRESSION_TESTS.xml',303))
    history();selection=seals.verify_active_code(baseline_seal=seals.CURRENT,proposed_transition=seals.PROPOSAL)
    # Only active baseline selection changed in the nine business regressions.
    for filename in ['test_ltt_uss_run2_offline.py','test_ltt_uss_full_qa_witness.py']:
        old=(P/'OLD_HARNESS/tests'/filename).read_text(encoding='utf8');new=(o.ROOT/'tests'/filename).read_text(encoding='utf8')
        assert new.replace("(baseline_seal='CURRENT_ACCEPTED_PRE_BATCH_SEAL', proposed_transition='TERMINAL_BATCH_FIX_PROPOSAL')",'()')==old
    old={a['index']:a for a in r.load(b.PHASE/'BEFORE_407.json')};comparison=[];affected=[];counts=Counter()
    with o.offline_guard() as network:
      for path,row in b.rows():
        if row['supplier']!='uss_stanko':continue
        i=row['index'];p,status,diag,outcome=b.evaluate(row);payload=asdict(p);sha=r.digest(r.encoded(payload));a=old[i]
        changed=sha!=a['payload_sha256'];counts[outcome]+=1
        comparison.append(dict(index=i,url=row['url'],old_outcome=a['outcome'],new_outcome=outcome,payload_changed=changed,old_payload_sha256=a['payload_sha256'],new_payload_sha256=sha))
        assert changed==(i in b.EXPECTED) and outcome==('PERSISTED' if i in b.EXPECTED else a['outcome']),('UNEXPECTED_SCOPE',i)
        if changed:
            assert a['outcome']=='AMBIGUOUS' and p.site_internal_id and p.name
            m=row['result'];capture=r.HttpCapture(requested_url=row['url'],final_url=m['final_url'],http_status=m['http_status'],content_type=m['content_type'],observed_at=datetime.fromisoformat(m['observed_at']),response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],evidence_ref=m['evidence_ref'],redirects=tuple(m['redirects']))
            meta=copy.deepcopy(m);meta.update(status=status.value,diagnostics=list(diag),capture=asdict(capture))
            revision=copy.deepcopy(row);revision.update(phase='PREPARED',outcome='PERSISTED',result=meta,external_id=p.site_internal_id,product_payload_sha256=sha,capture_fingerprint=capture.fingerprint,
                reviewed_extraction=dict(original_journal=str(path.relative_to(o.ROOT)),original_journal_sha256=r.digest(path.read_bytes()),old_outcome=a['outcome'],original_observed_at=m['observed_at'],reason='CONFIRMED_TERMINAL_BATCH_COVERAGE_DEFECT',supplier_http=0))
            r.product_card_from_snapshot(p,capture);r.observation_from_snapshot(p,capture)
            dest=P/'REVISED_ROWS/uss_stanko'/path.name;r.save(dest,revision)
            affected.append(dict(index=i,url=row['url'],external_id=p.site_internal_id,title=p.name,model=p.supplier_model,article=p.manufacturer_article,price=str(p.price.current_price) if p.price.current_price is not None else None,price_state=p.price.state.value,currency=p.price.currency,availability=p.availability,raw_price=p.price.raw_text,raw_availability=p.enrichment_evidence['availability_raw'],original_observed_at=m['observed_at'],response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],revision=str(dest.relative_to(P)),old_payload=a['payload'],new_payload=payload,original_journal=str(path.relative_to(o.ROOT)),original_journal_sha256=r.digest(path.read_bytes())))
        if len(comparison)%50==0:print(f'FRESH GATED USS IMPACT {len(comparison)}/407',flush=True)
    assert len(comparison)==407 and {a['index'] for a in affected}==b.EXPECTED
    assert counts==Counter(PERSISTED=227,AMBIGUOUS=114,MULTI_VARIANT=66)
    assert next(a for a in affected if a['index']==53)['price']=='1440'
    assert next(a for a in affected if a['index']==65)['price'] is None and next(a for a in affected if a['index']==65)['price_state']=='missing'
    history();assert r.code_hashes()==selection['working_code_hashes']
    r.save(P/'TEST_RECEIPT.json',tests);r.save(P/'IMPACT_COMPARISON_407.json',dict(status='PASS',total=407,changed_indices=sorted(b.EXPECTED),additional_scope=0,existing_persisted_payload_changes=0,valid_ambiguous_payload_changes=0,operator_ambiguous_changes=0,multi_variant_payload_changes=0,outcomes=counts,rows=comparison,network=network))
    r.save(P/'AFFECTED_ROWS.json',affected);r.save(P/'TERMINAL_BATCH_FIX_CODE_SEAL.json',selection['working_code_hashes'])
    harness={name:r.digest((o.ROOT/name).read_bytes()) for name in ['scripts/run_ltt_uss_run2_offline.py','scripts/ltt_uss_replay_seals.py','scripts/qa_ltt_uss_frozen_witness.py','tests/test_ltt_uss_run2_offline.py','tests/test_ltt_uss_full_qa_witness.py','tests/test_replay_seal_loader.py']}
    transition=dict(status='ACCEPTED_TESTS_AND_407_IMPACT_VERIFIED',OLD=seals.CURRENT,NEW='TERMINAL_BATCH_FIX_CODE_SEAL',old_code_hashes=seals.baseline_evidence(seals.CURRENT),new_code_hashes=selection['working_code_hashes'],accepted_seal_file='TERMINAL_BATCH_FIX_CODE_SEAL.json',accepted_seal_sha256=r.digest((P/'TERMINAL_BATCH_FIX_CODE_SEAL.json').read_bytes()),provenance_sha256=seals.PROVENANCE_SHA256,harness_hashes=harness,tests=tests,impact='IMPACT_COMPARISON_407.json',prior_failed_transition=str((b.PHASE/'CLUSTER_TRANSITIONS.json').relative_to(o.ROOT)),checked_at=r.now())
    r.save(P/'BATCH_CODE_TRANSITION.json',transition)
    prep=dict(status='OFFLINE_PREPARATION_PASS',new_code_hashes=selection['working_code_hashes'],accepted_transition='BATCH_CODE_TRANSITION.json',transition_sha256=r.digest((P/'BATCH_CODE_TRANSITION.json').read_bytes()),harness_hashes=harness,tests=tests,affected_indices=sorted(b.EXPECTED),network=network,db_started=False,checked_at=r.now())
    r.save(P/'OFFLINE_PREPARATION.json',prep)
    print(dict(status=prep['status'],tests='9+18+303 PASS',affected=sorted(b.EXPECTED),new_code_seal_sha256=transition['accepted_seal_sha256']),flush=True)

def effective_rows():
    affected={a['index']:a for a in r.load(P/'AFFECTED_ROWS.json')}
    for path,row in b.rows():
        if row['supplier']=='uss_stanko' and row['index'] in affected:
            path=P/affected[row['index']]['revision'];row=r.load(path)
        yield path,row

def execute():
    prep=r.load(P/'OFFLINE_PREPARATION.json');assert prep['status']=='OFFLINE_PREPARATION_PASS'
    assert r.code_hashes()==prep['new_code_hashes']
    assert r.digest((P/'BATCH_CODE_TRANSITION.json').read_bytes())==prep['transition_sha256']
    for k,v in prep['harness_hashes'].items():assert r.digest((o.ROOT/k).read_bytes())==v
    history();run=o.frozen_run(baseline_seal=seals.CURRENT,proposed_transition=seals.PROPOSAL)
    assert not (P/'BEFORE_CORRECTION.json').exists(),'Inspect durable state; do not silently repeat backfill'
    _,cfg=r.config();assert (cfg['host'],cfg['port'],cfg['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    last=None;repaired=[]
    with o.offline_guard() as network:
      repo=r.PostgresRepository(r.PostgresConfig(**cfg));c=repo.connection
      try:
        system=r.verify_system();c.execute("SET application_name='uss_terminal_batch_saved_evidence'")
        c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        before=o.snapshot(c);baseline=r.load(b.q.BASELINE)
        assert not [k for k in before if r.encoded(before[k])!=r.encoded(baseline[k])],'Accepted pre-batch baseline drift'
        assert before['totals']==dict(source_products=2445,offers=2445,supplier_http_captures=2525,offer_commercial_observations=2525)
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        b.accepted.integrity(c);o.clients(c)
        r.save(P/'BEFORE_CORRECTION.json',dict(**before,**system,checked_at=r.now()))
        affected=r.load(P/'AFFECTED_ROWS.json');allowed={a['external_id'] for a in affected};assert len(allowed)==8
        for a in affected:
            assert not c.execute('SELECT p.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code=%s AND p.external_id=%s',('uss_stanko',a['external_id'])).fetchall()
        for a in affected:
            last=dict(index=a['index'],url=a['url']);path=P/a['revision'];row=r.load(path)
            outcome,p,capture=o.extract(row);assert outcome=='PERSISTED' and p.supplier=='uss_stanko' and p.site_internal_id==a['external_id']
            assert str(capture.observed_at)==a['original_observed_at']
            assert capture.response_sha256==a['response_sha256'] and capture.evidence_sha256==a['evidence_sha256']
            ids,recovered=r.guarded_commit(repo,p,capture);assert not recovered
            r.verify_persisted(repo,p,capture);assert r.check_observation(repo,p,capture)==ids
            row.update(db_verification=ids,phase='TERMINAL',completed_at=r.now());r.save(path,row)
            repaired.append(dict(index=a['index'],external_id=a['external_id'],original_observed_at=a['original_observed_at'],response_sha256=a['response_sha256'],evidence_sha256=a['evidence_sha256'],price=a['price'],currency=a['currency'],price_state=a['price_state'],raw_price=a['raw_price'],raw_availability=a['raw_availability'],db_verification=ids))
            r.save(P/'BACKFILL_PROGRESS.json',dict(status='BACKFILL_RUNNING',rows=repaired,checked_at=r.now()))
            print(f'EXACT BACKFILL {a["index"]} VERIFIED',flush=True)
        after=o.snapshot(c);assert len(repaired)==8
        assert after['totals']=={k:v+8 for k,v in before['totals'].items()}
        protected=[k for k in before['fingerprints'] if k not in r.ALLOWED];assert len(protected)==46
        assert all(before['fingerprints'][k]==after['fingerprints'][k] for k in protected+['suppliers'])
        assert all(before['supplier_namespaces'][s]==after['supplier_namespaces'][s] for s in r.SOURCES if s!='uss_stanko')
        assert all(r.encoded(before[k])==r.encoded(after[k]) for k in ['special_products','sib_units'])
        previous={p['id']:(p,offer) for p,offer in before['product_offer_projections']};current={p['id']:(p,offer) for p,offer in after['product_offer_projections']}
        assert all(current[k]==v for k,v in previous.items())
        assert {p['external_id'] for p,offer in after['product_offer_projections'] if p['id'] not in previous}==allowed
        r.save(P/'AFTER_BACKFILL.json',dict(**after,**system,checked_at=r.now()))
        r.save(P/'BACKFILL_RECEIPT.json',dict(status='EXACT_EIGHT_SAVED_EVIDENCE_BACKFILLS_VERIFIED',rows=repaired,new_source_products=8,new_offers=8,new_captures=8,new_observations=8,old_ambiguous_history_preserved=True,identity_overwrite=0,foreign_mutations=0,original_time_hash_raw_preserved=True,supplier_http=0))
        counts={s:Counter() for s in r.SOURCES};plan=[];checked=0;terminal=[]
        accepted_amb={d['index']:d for d in r.load(b.PHASE/'TERMINAL_INVENTORY_OPERATOR_RESOLVED.json')['rows']}
        accepted_multi={d['index']:d for d in r.load(b.sweep.OUT/'USS_MULTI_VARIANT_66_DECISIONS.json')['rows']}
        for path,row in effective_rows():
            counts[row['supplier']][row['outcome']]+=1
            plan.append(dict(supplier=row['supplier'],index=row['index'],url=row['url'],outcome=row['outcome'],external_id=row.get('external_id'),journal=str(path.relative_to(o.ROOT)),journal_sha256=r.digest(path.read_bytes())))
            if row['outcome']=='PERSISTED':
                p,capture=run.product(row);assert r.check_observation(repo,p,capture)==row['db_verification'];r.verify_persisted(repo,p,capture);checked+=1
            elif row['supplier']=='uss_stanko':
                d=accepted_multi[row['index']] if row['outcome']=='MULTI_VARIANT' else accepted_amb[row['index']]
                assert d['decision'].startswith('VALID_') and not d.get('confirmed_defect',False)
                terminal.append(dict(index=row['index'],outcome=row['outcome'],decision=d['decision'],reason=d['semantic_reason'],source_id=d['source_id'],url=d['url'],decision_evidence=d['source_evidence'],operator_approved=row['index'] in b.OPERATOR))
            if len(plan)%400==0:print(f'ALL-SOURCE RECONCILIATION {len(plan)}/2634',flush=True)
        assert len(plan)==2634 and checked==2453
        assert counts['uss_stanko']==Counter(PERSISTED=227,AMBIGUOUS=114,MULTI_VARIANT=66)
        assert {s:sum(v.values()) for s,v in counts.items()}==dict(ltt_msk=369,ltt_sib=1266,ltt_yug=592,uss_stanko=407)
        assert len(terminal)==180 and {d['index'] for d in terminal if d['operator_approved']}==b.OPERATOR
        checks=b.accepted.integrity(c)
        r.save(P/'REVISED_TERMINAL_INVENTORY.json',dict(ambiguous=114,multi_variant=66,unreviewed_ambiguous=0,unreviewed_multi_variant=0,operator_accepted_indices=sorted(b.OPERATOR),rows=terminal))
        r.save(P/'EFFECTIVE_ROW_PLAN.json',dict(format='REVIEWED_POST_TERMINAL_BATCH_EFFECTIVE_ROWS_V1',total=2634,rows=plan))
        r.save(P/BASELINE_NAME,dict(**after,**system,status='REVIEWED_TERMINAL_BATCH_BASELINE',sources=counts,persisted=checked,total_scope=2634,ambiguous=114,multi_variant=66,non_product=1,pending=0,new_code_hashes=prep['new_code_hashes'],effective_row_plan='EFFECTIVE_ROW_PLAN.json',terminal_inventory='REVISED_TERMINAL_INVENTORY.json',protected_table_delta=0,foreign_mutations=0,identity_overwrite=0,duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,broken_references=checks,checked_at=r.now()))
        r.save(P/'RUN2/BEFORE.json',dict(**after,checked_at=r.now()))
        noop=r.PostgresRepository.__new__(r.PostgresRepository);noop.connection=o.NoMutationConnection(c)
        totals={s:Counter() for s in r.SOURCES};processed=noops=0
        terminal_lookup={d['index']:d for d in terminal}
        for e in plan:
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];row=r.load(path)
            outcome,p,capture=o.extract(row);assert outcome==e['outcome']
            decision=None
            if p is not None:
                r.verify_persisted(noop,p,capture);assert r.check_observation(noop,p,capture)==row['db_verification']
                receipt=noop.persist_commercial_observation(p,capture)
                assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
                assert all(v==0 for v in asdict(receipt['ingest_counts']).values());noops+=1
            elif row['supplier']=='uss_stanko':decision=terminal_lookup[row['index']]['decision']
            totals[row['supplier']][outcome]+=1;processed+=1
            r.save(P/'RUN2/ROWS'/row['supplier']/path.name,dict(supplier=row['supplier'],index=row['index'],url=row['url'],run1_outcome=row['outcome'],run2_outcome=outcome,semantic_decision=decision,persistence='EXACT_NOOP' if p else 'TERMINAL_STABLE',journal_sha256=e['journal_sha256']))
            if processed%200==0:
                r.save(P/'CHECKPOINT.json',dict(status='TERMINAL_BATCH_FULL_RUN2_RUNNING',processed=processed,total=2634,safe_to_power_off=False,updated_at=r.now()))
                print(f'NEW FULL OFFLINE RUN2 {processed}/2634',flush=True)
        final=o.snapshot(c);r.save(P/'RUN2/AFTER.json',dict(**final,checked_at=r.now()))
        assert not b.accepted.f.verify_unchanged(after,final) and totals==counts and noops==2453 and processed==2634
        history();assert r.code_hashes()==prep['new_code_hashes'];clients=o.clients(c)
        result=dict(status='LTT_USS_TERMINAL_BATCH_FIX_AND_RUN2_REVERIFIED',seal_loader_fix='VERIFIED',original_run1_seal_preserved=True,previous9_fail='RESOLVED',tests=prep['tests'],rows_fixed=sorted(b.EXPECTED),operator_valid_ambiguous=sorted(b.OPERATOR),sources=totals,counts=final['totals'],run2_scope=processed,run2_exact_noop_persisted=noops,run2_terminal_delta=0,run2_new_source_products=0,run2_new_offers=0,run2_new_captures=0,run2_new_observations=0,table_fingerprint_delta=0,sequence_delta=0,protected_table_delta=0,foreign_mutations=0,identity_overwrite=0,duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,broken_references=checks,unreviewed_ambiguous=0,unreviewed_multi_variant=0,network=network,clients=clients,full_qa_resumed=False,finalizer_started=False,matching_run=False,offer_selection_run=False,sterbrust_writes=0,esol_writes=0,fx_conversion='NONE',safe_to_power_off=False,next='CLEAN_SHUTDOWN_REQUIRED',checked_at=r.now())
        for name in ['RUN2/RESULT.json','RESULT.json','CHECKPOINT.json']:r.save(P/name,result)
        r.save(P/'HTTP_ZERO_PROOF.json',network)
        print(dict(status=result['status'],counts=result['counts'],run2=processed,exact_noop=noops,clients=clients),flush=True)
      except BaseException as error:
        r.save(P/'BLOCKER.json',dict(status='LTT_USS_TERMINAL_BATCH_FIX_BLOCKED',stage='BACKFILL_RECONCILIATION_OR_RUN2',last=last,error=f'{type(error).__name__}:{error}',db_started=True,db_correction_applied=bool(repaired),applied_rows=repaired,network=network,safe_to_power_off=False,checked_at=r.now()))
        raise
      finally:repo.close()

if __name__=='__main__':
    (prepare if sys.argv[1]=='prepare' else execute)()
