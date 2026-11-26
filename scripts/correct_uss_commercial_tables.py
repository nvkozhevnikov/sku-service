"""Reviewed saved-evidence USS correction, revised baseline, full offline RUN2.

Old RUN1/RUN2 journals and code seals are immutable. Only phase-local revised
rows overlay them. HTTP is prohibited; backfill creates previously absent exact
historical events, then ordinary writer replay must be an exact no-op.
"""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import copy
import sys
import argparse
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run2_offline as o
from universal_supplier.ltt_uss_capture import _card_gate
r=o.r
PHASE=r.BASE/'USS_TABLE_FIX_2026-10-15'
ADAPTER='universal_supplier/adapters/uss_stanko.py'


def historical_run():
    run=r.Run.__new__(r.Run)
    run.state=r.load(r.OUT/'CHECKPOINT.json')
    _,run.scopes=r.frozen_inputs()
    return run


def verify_history():
    inventory=o.inventory();old=r.load(o.OUT/'FROZEN_INPUTS.json')
    assert set(inventory)==set(old)
    adapter_key=next(k for k in old if Path(k).as_posix()==ADAPTER)
    assert all(inventory[k]==old[k] for k in old if k!=adapter_key)
    assert r.digest((PHASE/'OLD_USS_ADAPTER.py').read_bytes())==old[adapter_key]['sha256']


def outcome_for(row):
    result=row['result']
    body=(r.OUT/'EVIDENCE'/row['supplier']/(result['evidence_sha256']+'.html')).read_bytes()
    assert r.digest(body)==result['evidence_sha256']
    status,diagnostics=_card_gate(row['supplier'],body.decode('utf8'),result['final_url'])
    outcome='PERSISTED' if status.value=='SUCCESS' else ('MULTI_VARIANT' if 'MULTI_VARIANT' in diagnostics else status.value)
    return body,status,diagnostics,outcome


def prepare():
    assert not (PHASE/'OFFLINE_PREPARATION.json').exists()
    verify_history();run=historical_run();before=r.load(PHASE/'IMPACT_AUDIT_BEFORE_FIX.json')
    assert before['status']=='ALL_407_AUDITED_BEFORE_PARSER_CHANGE' and len(before['rows'])==407
    old={p['index']:p for p in before['rows']};affected=[];counts=Counter();unchanged=0
    with o.offline_guard() as network:
        for path,row in o.journals(run):
            if row['supplier']!='uss_stanko':continue
            body,status,diagnostics,outcome=outcome_for(row)
            planned=old[row['index']]
            assert outcome==planned['expected_new_outcome'],('UNEXPECTED_SELECTOR_IMPACT',row['index'],outcome)
            product=r.parse('uss_stanko',body,row['result']['final_url'])
            payload=r.digest(r.encoded(asdict(product)))
            counts[outcome]+=1
            if outcome==row['outcome']:
                assert payload==planned['old_payload_sha256'],('EXISTING_BEHAVIOR_CHANGED',row['index'])
                unchanged+=1
                continue
            assert row['outcome']=='AMBIGUOUS' and outcome=='PERSISTED'
            assert planned['strong_single_candidates']==planned['nondata_candidates']==1
            metadata=copy.deepcopy(row['result'])
            capture=r.HttpCapture(requested_url=row['url'],final_url=metadata['final_url'],
                http_status=metadata['http_status'],content_type=metadata['content_type'],
                observed_at=datetime.fromisoformat(metadata['observed_at']),response_sha256=metadata['response_sha256'],
                evidence_sha256=metadata['evidence_sha256'],evidence_ref=metadata['evidence_ref'],redirects=tuple(metadata['redirects']))
            metadata.update(status='SUCCESS',diagnostics=list(diagnostics),capture=asdict(capture))
            revision=copy.deepcopy(row)
            revision.update(phase='PREPARED',outcome='PERSISTED',result=metadata,external_id=product.site_internal_id,
                product_payload_sha256=payload,capture_fingerprint=capture.fingerprint,
                reviewed_extraction=dict(old_outcome=row['outcome'],original_journal=str(path.relative_to(r.OUT)),
                    original_journal_sha256=r.digest(path.read_bytes()),original_observed_at=str(capture.observed_at),
                    reason='VERIFIED_SELECTOR_COVERAGE_DEFECT',supplier_http=0))
            assert product.manufacturer_article and product.supplier_model
            r.product_card_from_snapshot(product,capture);r.observation_from_snapshot(product,capture)
            revised=PHASE/'REVISED_ROWS/uss_stanko'/path.name;r.save(revised,revision)
            affected.append(dict(index=row['index'],external_id=product.site_internal_id,url=row['url'],
                old_outcome='AMBIGUOUS',new_outcome='PERSISTED',price=str(product.price.current_price),currency=product.price.currency,
                availability=product.availability,model=product.supplier_model,article=product.manufacturer_article,
                revision=str(revised.relative_to(PHASE)),evidence_sha256=metadata['evidence_sha256'],
                response_sha256=metadata['response_sha256'],original_time=str(capture.observed_at),
                evidence_reason='single own model/article/price/availability/order anchor, matching title; unique coherent table'))
    assert {a['index'] for a in affected}=={23,24}
    assert counts==Counter(PERSISTED=214,AMBIGUOUS=127,MULTI_VARIANT=66)
    r.save(PHASE/'AFFECTED_ROWS.json',affected)
    report=dict(status='OFFLINE_PREPARATION_PASS',uss_total=407,
        classless_table_pages=sum(p['classless_table_count']>0 for p in before['rows']),
        coherent_nondata_candidate_pages=sum(p['nondata_candidates']>0 and not p['data_table_count'] for p in before['rows']),
        current_false_ambiguous_confirmed=len(affected),current_multi_variant_affected=0,
        current_persisted_behavior_changed=0,unchanged_payloads=unchanged,review_only=13,
        revised_counts=counts,network=network,old_code_hashes=r.load(PHASE/'OLD_CODE_SEAL.json'),new_code_hashes=r.code_hashes(),checked_at=r.now())
    r.save(PHASE/'OFFLINE_PREPARATION.json',report)
    r.save(PHASE/'TRANSITION.json',dict(old='USS_CLASS_DATA_ONLY_COMMERCIAL_TABLE_SELECTION',new='SEMANTIC_COMMERCIAL_TABLE_SELECTION',
        root_cause='VERIFIED_SELECTOR_COVERAGE_DEFECT',**report,affected_rows='AFFECTED_ROWS.json',historical_journals_unchanged=True))
    print(r.encoded(report).decode(),flush=True)


def effective_rows(run):
    revisions={a['index']:a for a in r.load(PHASE/'AFFECTED_ROWS.json')}
    for path,row in o.journals(run):
        if row['supplier']=='uss_stanko' and row['index'] in revisions:
            path=PHASE/revisions[row['index']]['revision'];row=r.load(path)
        yield path,row


def verify_unchanged(before,after):
    return [k for k in before if r.encoded(before[k])!=r.encoded(after[k])]


def execute():
    prep=r.load(PHASE/'OFFLINE_PREPARATION.json');assert prep['status']=='OFFLINE_PREPARATION_PASS'
    tests=r.load(PHASE/'TEST_RECEIPT.json');assert tests['fail']==tests['error']==tests['skip']==0
    assert tests['focused_pass']>0 and tests['regression_pass']>0
    assert prep['new_code_hashes']==r.code_hashes()
    assert not (PHASE/'BEFORE_CORRECTION.json').exists(),'Inspect existing correction checkpoint; never auto-repeat'
    verify_history();run=historical_run();_,cfg=r.config();repo=r.PostgresRepository(r.PostgresConfig(**cfg));c=repo.connection
    status='CORRECTION_RUNNING';last=None
    with o.offline_guard() as network:
      try:
        system=r.verify_system();c.execute("SET application_name='uss_table_saved_correction'");c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        before=o.snapshot(c)
        assert before['fingerprints']==r.load(o.OUT/'AFTER_RUN2.json')['fingerprints']
        assert before['totals']==r.load(o.OUT/'AFTER_RUN2.json')['totals']
        r.save(PHASE/'BEFORE_CORRECTION.json',dict(**before,**system,checked_at=r.now()))
        affected=r.load(PHASE/'AFFECTED_ROWS.json');repaired=[]
        allowed_ids={a['external_id'] for a in affected}
        for item in affected:
            last=item;path=PHASE/item['revision'];row=r.load(path)
            product,capture=run.product(row)
            assert product.site_internal_id in allowed_ids
            assert not c.execute('''SELECT p.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code=%s AND p.external_id=%s''',('uss_stanko',product.site_internal_id)).fetchall()
            ids,recovered=r.guarded_commit(repo,product,capture);assert not recovered
            assert r.verify_persisted(repo,product,capture)
            row.update(db_verification=ids,phase='TERMINAL',completed_at=r.now());r.save(path,row)
            repaired.append(dict(**item,db_verification=ids))
            r.save(PHASE/'BACKFILL_PROGRESS.json',dict(status=status,rows=repaired))
        after=o.snapshot(c)
        assert after['totals']==dict(source_products=2440,offers=2440,supplier_http_captures=2520,offer_commercial_observations=2520)
        assert all(before['fingerprints'][k]==after['fingerprints'][k] for k in before['fingerprints'] if k not in r.ALLOWED)
        assert before['fingerprints']['suppliers']==after['fingerprints']['suppliers']
        assert all(before['supplier_namespaces'][s]==after['supplier_namespaces'][s] for s in r.SOURCES if s!='uss_stanko')
        previous={p['id']:(p,offer) for p,offer in before['product_offer_projections']}
        current={p['id']:(p,offer) for p,offer in after['product_offer_projections']}
        assert all(current[k]==v for k,v in previous.items())
        assert {p['external_id'] for p,offer in after['product_offer_projections'] if p['id'] not in previous}==allowed_ids
        counts={s:Counter() for s in r.SOURCES}
        for path,row in effective_rows(run):
            counts[row['supplier']][row['outcome']]+=1
            if row['outcome']=='PERSISTED':
                product,capture=run.product(row)
                assert r.check_observation(repo,product,capture)==row['db_verification']
                r.verify_persisted(repo,product,capture)
        assert sum(sum(v.values()) for v in counts.values())==2634
        r.save(PHASE/'BACKFILL_RECEIPT.json',dict(status='SAVED_EVIDENCE_BACKFILL_VERIFIED',rows=repaired,new_events=2,
            supplier_http=0,source_products_delta=2,offers_delta=2,captures_delta=2,observations_delta=2,
            historical_time_preserved=True,incorrect_existing_rows_overwritten=0,checked_at=r.now()))
        r.save(PHASE/'REVISED_RUN1_BASELINE.json',dict(status='REVIEWED_POST_QA_CORRECTION_BASELINE',**after,**system,
            sources=counts,total_scope=2634,persisted=2440,ambiguous=127,multi_variant=66,non_product=1,
            protected_table_delta=0,foreign_mutations=0,identity_overwrite=0,duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,checked_at=r.now()))
        # Full revised RUN2 starts only after all-source reconciliation. Its writer
        # is the ordinary repository behind the no-DML/no-sequence SQL filter.
        r.save(PHASE/'RUN2/BEFORE.json',dict(**after,checked_at=r.now()))
        noop=r.PostgresRepository.__new__(r.PostgresRepository);noop.connection=o.NoMutationConnection(c)
        totals={s:Counter() for s in r.SOURCES};processed=0
        for path,row in effective_rows(run):
            last=dict(supplier=row['supplier'],index=row['index'],url=row['url'])
            if row['outcome']=='NON_PRODUCT_CATEGORY_PAGE':
                outcome,product,capture=o.extract(row)
            else:
                body,gate,diagnostics,outcome=outcome_for(row)
                assert outcome==row['outcome']
                product,capture=run.product(row) if outcome=='PERSISTED' else (None,None)
                original=r.load(r.OUT/row['result']['response_record'])
                assert r.digest((r.OUT/original['raw_path']).read_bytes())==row['result']['response_sha256']
            if product is not None:
                r.verify_persisted(noop,product,capture)
                assert r.check_observation(noop,product,capture)==row['db_verification']
                receipt=noop.persist_commercial_observation(product,capture)
                assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
                assert all(v==0 for v in asdict(receipt['ingest_counts']).values())
            totals[row['supplier']][outcome]+=1;processed+=1
            r.save(PHASE/'RUN2/ROWS'/row['supplier']/path.name,dict(**last,run1_outcome=row['outcome'],run2_outcome=outcome,persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE'))
            if processed%200==0:
                r.save(PHASE/'CHECKPOINT.json',dict(status='REVISED_RUN2_RUNNING',processed=processed,total=2634,safe_to_power_off=False,updated_at=r.now()))
                print(f'REVISED RUN2 {processed}/2634',flush=True)
        final=o.snapshot(c);r.save(PHASE/'RUN2/AFTER.json',dict(**final,checked_at=r.now()))
        assert not verify_unchanged(after,final) and totals==counts
        verify_history();assert r.code_hashes()==prep['new_code_hashes']
        clients=o.clients(c)
        result=dict(status='LTT_USS_QA_BLOCKER_FIXED_AND_RUN2_REVERIFIED',run2_scope=2634,run2_exact_noop_persisted=2440,
            run2_terminal_delta=0,run2_new_products=0,run2_new_offers=0,run2_new_captures=0,run2_new_observations=0,
            table_fingerprint_delta=0,sequence_delta=0,database_delta=[],sources=totals,counts=final['totals'],network=network,
            corrected_rows=[a['index'] for a in affected],clients=clients,full_qa_resumed=False,finalizer_started=False,checked_at=r.now())
        r.save(PHASE/'RUN2/RESULT.json',result);r.save(PHASE/'RESULT.json',result)
        r.save(PHASE/'HTTP_ZERO_PROOF.json',network)
        r.save(PHASE/'CHECKPOINT.json',dict(**result,safe_to_power_off=False,next='CLEAN_SHUTDOWN_REQUIRED',worker_stopped=True))
        print(r.encoded(result).decode(),flush=True)
      except Exception as error:
        r.save(PHASE/'BLOCKER.json',dict(status='LTT_USS_QA_BLOCKER_FIX_BLOCKED',row=last,error=f'{type(error).__name__}:{error}',network=network,checked_at=r.now()))
        raise
      finally:repo.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','execute'));args=p.parse_args()
    (prepare if args.mode=='prepare' else execute)()
