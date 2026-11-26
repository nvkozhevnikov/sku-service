"""Operator-scoped URL handoff, five historical backfills and full offline replay."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import copy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import correct_uss_own_heading as h
r, o, f = h.r, h.o, h.f
PHASE = r.BASE/'USS_H2_URL_HANDOFF_2026-10-15'
EXPECTED = {368,369,374,377,384}
BASELINE = f.PHASE/'REVISED_RUN1_BASELINE_AFTER_QA_FIX.json'
GATE = 'universal_supplier/ltt_uss_capture.py'


def verify_history():
    inventory=o.inventory();old=r.load(o.OUT/'FROZEN_INPUTS.json')
    assert set(inventory)==set(old)
    allowed={f.ADAPTER,GATE}
    assert all(inventory[k]==old[k] for k in old if Path(k).as_posix() not in allowed)
    for name,archive in [(f.ADAPTER,f.PHASE/'OLD_USS_ADAPTER.py'),(GATE,PHASE/'OLD_CARD_GATE.py')]:
        key=next(k for k in old if Path(k).as_posix()==name)
        assert r.digest(archive.read_bytes())==old[key]['sha256']
    assert {str(p.relative_to(h.PHASE)):r.digest(p.read_bytes()) for p in h.PHASE.rglob('*') if p.is_file()}==r.load(PHASE/'HISTORICAL_H2_SEAL.json')


def effective_rows():
    affected={a['index']:a for a in r.load(PHASE/'AFFECTED_ROWS.json')}
    for path,row in h.effective_rows():
        if row['supplier']=='uss_stanko' and row['index'] in affected:
            path=PHASE/affected[row['index']]['revision'];row=r.load(path)
        yield path,row


def prepare():
    assert not (PHASE/'OFFLINE_PREPARATION.json').exists()
    tests=r.load(PHASE/'TEST_RECEIPT.json')
    assert tests['focused_pass']==21 and tests['regression_pass']>0 and tests['fail']==tests['error']==tests['skip']==0
    before=r.load(h.PHASE/'TITLE_AUDIT_BEFORE.json');old={a['index']:a for a in before['rows']}
    affected=[];comparison=[];counts=Counter();changed=Counter()
    with o.offline_guard() as network:
      for path,row in h.effective_rows():
        if row['supplier']!='uss_stanko':continue
        a=old[row['index']];m=row['result']
        response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes()
        assert r.digest(raw)==m['response_sha256']==response['response_sha256']
        body=(r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
        p=r.parse('uss_stanko',body,m['final_url'])
        status,diags=f._card_gate('uss_stanko',raw.decode(m['charset'] or 'utf8','replace'),m['final_url'])
        outcome='PERSISTED' if status.value=='SUCCESS' else ('MULTI_VARIANT' if 'MULTI_VARIANT' in diags else status.value)
        assert outcome==a['expected_new_outcome'],('UNEXPECTED_IMPACT',row['index'],outcome)
        payload=asdict(p);sha=r.digest(r.encoded(payload));different=sha!=a['old_payload_sha256']
        assert different==(row['index'] in EXPECTED)
        counts[outcome]+=1
        comparison.append(dict(index=row['index'],url=row['url'],source_id=p.site_internal_id,old_outcome=row['outcome'],
            new_outcome=outcome,old_payload_sha256=a['old_payload_sha256'],new_payload_sha256=sha,payload_changed=different,
            h1_payload_unchanged=not different if a['h1_count'] else None,original_journal=str(path.relative_to(o.ROOT)),
            original_journal_sha256=r.digest(path.read_bytes()),evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256']))
        if different:
            changed[row['outcome']]+=1
            assert row['outcome']=='AMBIGUOUS' and outcome=='PERSISTED'
            assert a['classification']=='CONFIRMED_TITLE_COVERAGE_DEFECT'
            assert p.name==a['expected_own_heading'] and p.site_internal_id==a['source_id']
            assert p.supplier_model==a['tables'][0]['models'][0][1]
            assert p.manufacturer_article==a['tables'][0]['articles'][0][1]
            assert p.price.raw_text==a['old_payload']['price']['raw_text'] and p.price.current_price is not None
            assert p.enrichment_evidence['availability_raw']==a['old_payload']['enrichment_evidence']['availability_raw']
            capture=r.HttpCapture(requested_url=row['url'],final_url=m['final_url'],http_status=m['http_status'],
                content_type=m['content_type'],observed_at=datetime.fromisoformat(m['observed_at']),
                response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],evidence_ref=m['evidence_ref'],redirects=tuple(m['redirects']))
            metadata=copy.deepcopy(m);metadata.update(status=status.value,diagnostics=list(diags),capture=asdict(capture))
            revision=copy.deepcopy(row)
            revision.update(phase='PREPARED',outcome='PERSISTED',result=metadata,external_id=p.site_internal_id,
                product_payload_sha256=sha,capture_fingerprint=capture.fingerprint,
                reviewed_extraction=dict(old_outcome=row['outcome'],original_journal=str(path.relative_to(o.ROOT)),
                    original_journal_sha256=r.digest(path.read_bytes()),original_observed_at=m['observed_at'],
                    reason='VERIFIED_TITLE_SELECTOR_COVERAGE_DEFECT_WITH_SOURCE_URL_HANDOFF',supplier_http=0))
            r.product_card_from_snapshot(p,capture);r.observation_from_snapshot(p,capture)
            dest=PHASE/'REVISED_ROWS/uss_stanko'/path.name;r.save(dest,revision)
            affected.append(dict(index=row['index'],external_id=p.site_internal_id,url=row['url'],title=p.name,model=p.supplier_model,
                article=p.manufacturer_article,price=str(p.price.current_price),currency=p.price.currency,availability=p.availability,
                raw_price=p.price.raw_text,raw_availability=p.enrichment_evidence['availability_raw'],original_time=m['observed_at'],
                evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],old_outcome=row['outcome'],new_outcome=outcome,
                revision=str(dest.relative_to(PHASE)),old_payload=a['old_payload'],new_payload=payload))
        if len(comparison)%50==0:print(f'TITLE IMPACT {len(comparison)}/407',flush=True)
      verify_history()
    assert len(comparison)==407 and {a['index'] for a in affected}==EXPECTED
    assert counts==Counter(PERSISTED=219,AMBIGUOUS=122,MULTI_VARIANT=66) and changed==Counter(AMBIGUOUS=5)
    assert next(a for a in comparison if a['index']==405)['new_outcome']=='AMBIGUOUS'
    seal=r.code_hashes();before_seal=r.load(PHASE/'OLD_CODE_SEAL.json')
    assert [k for k in seal if seal[k]!=before_seal[k]]==[GATE]
    r.save(PHASE/'AFFECTED_ROWS.json',affected);r.save(PHASE/'IMPACT_COMPARISON_407.json',comparison)
    r.save(PHASE/'NEW_CODE_SEAL.json',seal)
    report=dict(status='OFFLINE_PREPARATION_PASS',uss_total=407,confirmed_affected=sorted(EXPECTED),
        persisted_payload_changes=0,multi_variant_payload_changes=0,ambiguous_payload_changes=5,unrelated_title_promotions=0,
        h1_payloads_unchanged=sum(a['h1_payload_unchanged'] is True for a in comparison),
        row405='LEGITIMATE_AMBIGUOUS_UNCHANGED',revised_counts=counts,new_code_hashes=seal,network=network,checked_at=r.now())
    r.save(PHASE/'OFFLINE_PREPARATION.json',report)
    r.save(PHASE/'TRANSITION.json',dict(OLD='EVIDENCE_BACKED_OWN_HEADING_EXTRACTION_WITH_MISSING_URL_HANDOFF',
        NEW='EVIDENCE_BACKED_OWN_HEADING_EXTRACTION_WITH_SOURCE_URL_HANDOFF',
        ROOT_CAUSE='SOURCE_URL_NOT_FORWARDED_BY_EXISTING_USS_CARD_GATE',status='REPOSITORY_VERIFIED_TESTS_AND_407_IMPACT_PASS',
        old_code_hashes=before_seal,new_code_hashes=seal,previous_not_accepted_transition=str((h.PHASE/'TRANSITION.json').relative_to(o.ROOT)),
        impact='IMPACT_COMPARISON_407.json',tests='TEST_RECEIPT.json',affected='AFFECTED_ROWS.json'))
    print(r.encoded(report).decode(),flush=True)


def integrity(c):
    queries={
        'orphan_offers':'SELECT count(*) FROM offers o LEFT JOIN source_products p ON p.id=o.source_product_id WHERE p.id IS NULL',
        'orphan_products':'SELECT count(*) FROM source_products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE s.id IS NULL',
        'products_without_offer':'SELECT count(*) FROM source_products p LEFT JOIN offers o ON o.source_product_id=p.id WHERE o.id IS NULL',
        'orphan_captures':'SELECT count(*) FROM supplier_http_captures c LEFT JOIN source_products p ON p.id=c.source_product_id WHERE p.id IS NULL',
        'orphan_observations':'SELECT count(*) FROM offer_commercial_observations b LEFT JOIN supplier_http_captures c ON c.id=b.capture_id LEFT JOIN source_products p ON p.id=b.source_product_id WHERE c.id IS NULL OR p.id IS NULL',
        'foreign_capture_observations':'SELECT count(*) FROM offer_commercial_observations b JOIN supplier_http_captures c ON c.id=b.capture_id WHERE b.source_product_id<>c.source_product_id OR b.supplier_id<>c.supplier_id',
    }
    results={k:c.execute(v).fetchone()[0] for k,v in queries.items()}
    assert not any(results.values()),results
    return results


def execute():
    prep=r.load(PHASE/'OFFLINE_PREPARATION.json');assert prep['status']=='OFFLINE_PREPARATION_PASS'
    assert r.code_hashes()==prep['new_code_hashes']
    tests=r.load(PHASE/'TEST_RECEIPT.json');assert tests['fail']==tests['error']==tests['skip']==0
    assert not (PHASE/'BEFORE_CORRECTION.json').exists(),'Inspect durable progress; never silently repeat backfill'
    verify_history();run=f.historical_run();_,cfg=r.config()
    assert (cfg['host'],cfg['port'],cfg['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    last=None;repaired=[]
    with o.offline_guard() as network:
      repo=r.PostgresRepository(r.PostgresConfig(**cfg));c=repo.connection
      try:
        system=r.verify_system();c.execute("SET application_name='uss_h2_url_handoff_saved_only'")
        c.execute("SET statement_timeout='30s'");c.execute("SET lock_timeout='5s'")
        before=o.snapshot(c);baseline=r.load(BASELINE)
        assert not [k for k in before if r.encoded(before[k])!=r.encoded(baseline[k])],'Accepted baseline drift'
        assert len(before['fingerprints'])==51 and len(before['sequences'])==40
        assert before['totals']==dict(source_products=2440,offers=2440,supplier_http_captures=2520,offer_commercial_observations=2520)
        integrity(c);r.save(PHASE/'BEFORE_CORRECTION.json',dict(**before,**system,checked_at=r.now()))
        affected=r.load(PHASE/'AFFECTED_ROWS.json');allowed={a['external_id'] for a in affected}
        # Prove every allowed product absent before starting any correction.
        for a in affected:
            assert not c.execute('SELECT p.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code=%s AND p.external_id=%s',('uss_stanko',a['external_id'])).fetchall()
        for a in affected:
            last=dict(supplier='uss_stanko',index=a['index'],url=a['url']);path=PHASE/a['revision'];row=r.load(path)
            outcome,product,capture=o.extract(row);assert outcome=='PERSISTED' and product.site_internal_id in allowed
            ids,recovered=r.guarded_commit(repo,product,capture);assert not recovered
            r.verify_persisted(repo,product,capture)
            assert r.check_observation(repo,product,capture)==ids
            row.update(db_verification=ids,phase='TERMINAL',completed_at=r.now());r.save(path,row)
            repaired.append(dict(index=a['index'],external_id=a['external_id'],title=a['title'],model=a['model'],article=a['article'],
                price=a['price'],currency=a['currency'],availability=a['availability'],original_time=a['original_time'],db_verification=ids))
            r.save(PHASE/'BACKFILL_PROGRESS.json',dict(status='BACKFILL_RUNNING',rows=repaired,checked_at=r.now()))
            print(f"BACKFILL {a['index']} verified",flush=True)
        after=o.snapshot(c);n=len(repaired)
        assert n==5 and after['totals']=={k:v+n for k,v in before['totals'].items()}
        protected=[k for k in before['fingerprints'] if k not in r.ALLOWED];assert len(protected)==46
        assert all(before['fingerprints'][k]==after['fingerprints'][k] for k in protected+['suppliers'])
        assert all(before['supplier_namespaces'][s]==after['supplier_namespaces'][s] for s in r.SOURCES if s!='uss_stanko')
        for k in ['special_products','sib_units']:assert r.encoded(before[k])==r.encoded(after[k])
        previous={p['id']:(p,offer) for p,offer in before['product_offer_projections']}
        current={p['id']:(p,offer) for p,offer in after['product_offer_projections']}
        assert all(current[k]==v for k,v in previous.items())
        assert {p['external_id'] for p,offer in after['product_offer_projections'] if p['id'] not in previous}==allowed
        counts={s:Counter() for s in r.SOURCES};plan=[];checked=0
        for path,row in effective_rows():
            counts[row['supplier']][row['outcome']]+=1
            plan.append(dict(supplier=row['supplier'],index=row['index'],url=row['url'],outcome=row['outcome'],external_id=row.get('external_id'),
                journal=str(path.relative_to(o.ROOT)),journal_sha256=r.digest(path.read_bytes())))
            if row['outcome']=='PERSISTED':
                product,capture=run.product(row)
                assert r.check_observation(repo,product,capture)==row['db_verification'];r.verify_persisted(repo,product,capture);checked+=1
            if len(plan)%400==0:print(f'ALL SOURCE RECONCILIATION {len(plan)}/2634',flush=True)
        assert len(plan)==2634 and checked==2445
        assert counts['uss_stanko']==Counter(PERSISTED=219,AMBIGUOUS=122,MULTI_VARIANT=66)
        assert {s:sum(v.values()) for s,v in counts.items()}==dict(ltt_msk=369,ltt_sib=1266,ltt_yug=592,uss_stanko=407)
        checks=integrity(c)
        r.save(PHASE/'EFFECTIVE_ROW_PLAN.json',dict(format='REVIEWED_POST_H2_URL_HANDOFF_EFFECTIVE_ROWS_V1',total=2634,rows=plan))
        r.save(PHASE/'BACKFILL_RECEIPT.json',dict(status='SAVED_EVIDENCE_FIVE_BACKFILLS_VERIFIED',rows=repaired,new_source_products=n,new_offers=n,
            new_captures=n,new_observations=n,original_time_hash_raw_preserved=True,incorrect_existing_rows_overwritten=0,supplier_http=0))
        r.save(PHASE/'REVISED_RUN1_BASELINE_AFTER_H2_URL_HANDOFF_FIX.json',dict(**after,**system,status='REVIEWED_H2_URL_HANDOFF_BASELINE',
            sources=counts,persisted=checked,total_scope=2634,ambiguous=122,multi_variant=66,non_product=1,pending=0,
            new_code_hashes=prep['new_code_hashes'],effective_row_plan='EFFECTIVE_ROW_PLAN.json',
            protected_table_delta=0,foreign_mutations=0,identity_overwrite=0,duplicate_external_ids=0,duplicate_offer_links=0,
            unexplained_missing=0,broken_references=checks,checked_at=r.now()))
        r.save(PHASE/'RUN2/BEFORE.json',dict(**after,checked_at=r.now()))
        noop=r.PostgresRepository.__new__(r.PostgresRepository);noop.connection=o.NoMutationConnection(c)
        totals={s:Counter() for s in r.SOURCES};processed=noops=0
        for entry in plan:
            last=entry;path=o.ROOT/entry['journal'];assert r.digest(path.read_bytes())==entry['journal_sha256'];row=r.load(path)
            outcome,product,capture=o.extract(row)
            assert outcome==entry['outcome']
            if product is not None:
                r.verify_persisted(noop,product,capture)
                assert r.check_observation(noop,product,capture)==row['db_verification']
                receipt=noop.persist_commercial_observation(product,capture)
                assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
                assert all(v==0 for v in asdict(receipt['ingest_counts']).values());noops+=1
            totals[row['supplier']][outcome]+=1;processed+=1
            r.save(PHASE/'RUN2/ROWS'/row['supplier']/path.name,dict(supplier=row['supplier'],index=row['index'],url=row['url'],
                run1_outcome=row['outcome'],run2_outcome=outcome,persistence='EXACT_NOOP' if product else 'TERMINAL_STABLE',
                journal_sha256=entry['journal_sha256']))
            if processed%200==0:
                r.save(PHASE/'CHECKPOINT.json',dict(status='H2_REVISED_RUN2_RUNNING',processed=processed,total=2634,safe_to_power_off=False,updated_at=r.now()))
                print(f'NEW FULL OFFLINE RUN2 {processed}/2634',flush=True)
        final=o.snapshot(c);r.save(PHASE/'RUN2/AFTER.json',dict(**final,checked_at=r.now()))
        assert not f.verify_unchanged(after,final) and totals==counts and noops==2445 and processed==2634
        verify_history();assert r.code_hashes()==prep['new_code_hashes']
        clients=o.clients(c)
        result=dict(status='LTT_USS_H2_TITLE_FIX_AND_RUN2_REVERIFIED',source_url_handoff_fix='VERIFIED',uss_rows_audited=407,
            confirmed_title_defects=5,rows_fixed=sorted(EXPECTED),row405='LEGITIMATE_AMBIGUOUS_UNCHANGED',sources=totals,counts=final['totals'],
            run2_scope=processed,run2_exact_noop_persisted=noops,run2_terminal_delta=0,run2_new_products=0,run2_new_offers=0,
            run2_new_captures=0,run2_new_observations=0,table_fingerprint_delta=0,sequence_delta=0,
            protected_table_delta=0,foreign_mutations=0,identity_overwrite=0,duplicate_external_ids=0,duplicate_offer_links=0,
            unexplained_missing=0,broken_references=checks,network=network,clients=clients,tests=tests,
            full_qa_resumed=False,finalizer_started=False,matching_run=False,offer_selection_run=False,
            matching_sql_writes=0,catalog_link_writes=0,offer_selection_writes=0,sterbrust_writes=0,esol_writes=0,
            next='CLEAN_SHUTDOWN_REQUIRED',safe_to_power_off=False,checked_at=r.now())
        for name in ['RUN2/RESULT.json','RESULT.json','CHECKPOINT.json']:r.save(PHASE/name,result)
        r.save(PHASE/'HTTP_ZERO_PROOF.json',network)
        print(r.encoded(result).decode(),flush=True)
      except BaseException as error:
        r.save(PHASE/'BLOCKER.json',dict(status='LTT_USS_H2_TITLE_FIX_BLOCKED',stage='BACKFILL_OR_RUN2',row=last,
            error=f'{type(error).__name__}:{error}',db_started=True,db_correction_applied=bool(repaired),applied_rows=repaired,network=network,checked_at=r.now()))
        raise
      finally:repo.close()


if __name__=='__main__':
    (prepare if sys.argv[1]=='prepare' else execute)()
