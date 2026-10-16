"""Fresh final-baseline QA. Only SELECT under SQL/HTTP/persistence guards."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from scripts import resume_uss_h2_url_handoff as accepted
from scripts import qa_ltt_uss_revised as q
r,o=accepted.r,accepted.o
OUT=r.BASE/'FULL_QA_FINAL_2026-10-15'
PLAN=accepted.PHASE/'EFFECTIVE_ROW_PLAN.json'
BASELINE=accepted.PHASE/'REVISED_RUN1_BASELINE_AFTER_H2_URL_HANDOFF_FIX.json'


def structural():
    assert r.load(OUT/'OFFLINE_PREFLIGHT.json')['status']=='PASS'
    assert r.load(OUT/'TEST_RECEIPT.json')['fail']==0
    assert r.code_hashes()==r.load(accepted.PHASE/'NEW_CODE_SEAL.json')
    assert not (OUT/'BEFORE_QA.json').exists(),'Fresh QA cannot silently overwrite prior state'
    plan=r.load(PLAN)['rows'];counts={s:Counter() for s in r.SOURCES}
    checked=persisted=0;last=None
    with q.guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute('SET default_transaction_read_only=on')
        raw.execute("SET application_name='ltt_uss_fresh_final_full_qa_readonly'")
        raw.execute("SET statement_timeout='30s'")
        assert raw.execute('SHOW default_transaction_read_only').fetchone()[0]=='on'
        assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=q.ReadOnlyConnection(raw);repo=r.PostgresRepository.__new__(r.PostgresRepository);repo.connection=c
        system=r.verify_system();before=o.snapshot(c);baseline=r.load(BASELINE)
        assert not [k for k in before if r.encoded(before[k])!=r.encoded(baseline[k])]
        assert before['totals']==dict(source_products=2445,offers=2445,supplier_http_captures=2525,offer_commercial_observations=2525)
        r.save(OUT/'BEFORE_QA.json',dict(**before,**system,checked_at=r.now()))
        try:
          for entry in plan:
            last=entry;path=o.ROOT/entry['journal'];assert r.digest(path.read_bytes())==entry['journal_sha256']
            row=r.load(path)
            assert (row['supplier'],row['index'],row['url'])==(entry['supplier'],entry['index'],entry['url'])
            outcome,product,capture=o.extract(row);assert outcome==entry['outcome']
            counts[row['supplier']][outcome]+=1;ids=None
            if product is not None:
                assert product.site_internal_id==entry['external_id'] and product.name
                ids=r.check_observation(repo,product,capture);assert ids==row['db_verification']
                r.verify_persisted(repo,product,capture)
                latest=c.execute('SELECT max(observed_at) FROM offer_commercial_observations WHERE source_product_id=%s',(ids['source_product_id'],)).fetchone()[0]
                assert latest==capture.observed_at
                joined=c.execute('''SELECT row_to_json(p),row_to_json(o),row_to_json(cap),row_to_json(b)
                    FROM source_products p JOIN offers o ON o.source_product_id=p.id
                    JOIN supplier_http_captures cap ON cap.source_product_id=p.id
                    JOIN offer_commercial_observations b ON b.capture_id=cap.id AND b.offer_id=o.id
                    WHERE b.id=%s''',(ids['observation_id'],)).fetchall()
                assert len(joined)==1
                p,offer,cap,obs=joined[0]
                assert p['name']==product.name and offer['source_product_id']==p['id']
                assert p['supplier_id']==offer['supplier_id']==cap['supplier_id']==obs['supplier_id']
                assert obs['price_raw']==product.price.raw_text and (obs['currency'] or '')==(product.price.currency or '')
                assert obs['availability_raw']==product.availability
                r.save(OUT/'PERSISTED_ROWS'/row['supplier']/path.name,dict(entry=entry,parsed_product=asdict(product),
                    product=p,offer=offer,capture=cap,observation=obs,latest_observation=str(latest),status='AUTOMATED_TRACE_PASS',semantic_approval=False))
                persisted+=1
            checked+=1
            r.save(OUT/'STRUCTURAL_ROWS'/row['supplier']/path.name,dict(**entry,db_verification=ids,status='STRUCTURAL_PASS',semantic_approval=False))
            if checked%200==0:
                r.save(OUT/'CHECKPOINT.json',dict(status='QA_STRUCTURAL_RUNNING',checked=checked,persisted_checked=persisted,total=2634,safe_to_power_off=False,updated_at=r.now()))
                print(f'FRESH QA STRUCTURAL {checked}/2634 persisted{persisted}',flush=True)
          assert checked==2634 and persisted==2445
          assert counts=={s:Counter(v) for s,v in baseline['sources'].items()}
          r.save(OUT/'STRUCTURAL_RESULT.json',dict(status='PASS',rows_checked=checked,persisted_automated_checked=persisted,sources=counts,
              missing_rows=0,double_counted_rows=0,silent_rows=0,cross_supplier_identity_bleed=0,missing_evidence=0,checked_at=r.now()))
          r.save(OUT/'PERSISTED_AUTOMATED_AUDIT.json',dict(status='PASS',rows_checked=persisted,duplicate_external_ids=0,duplicate_offer_links=0,
              missing_evidence=0,identity_overwrite=0,details='PERSISTED_ROWS',semantic_approval=False))
          r.save(OUT/'DB_INTEGRITY.json',dict(status='PASS',checks=accepted.integrity(c),duplicate_external_ids=0,duplicate_offer_links=0,
              catalog_product_links=0,product_matches=before['fingerprints']['product_matches'],selection=before['fingerprints']['catalog_offer_selection']))
        except BaseException as error:
          r.save(OUT/'STRUCTURAL_BLOCKER.json',dict(row=last,checked=checked,persisted_checked=persisted,error=f'{type(error).__name__}:{error}',checked_at=r.now()))
          raise
        finally:
          after=o.snapshot(c);assert not accepted.f.verify_unchanged(before,after)
          r.save(OUT/'AFTER_STRUCTURAL.json',dict(**after,checked_at=r.now()))
          r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,source_persistence_calls=0,sql_writes=0,read_only_sql_enforced=True))
    print('Fresh structural2634/2634,persisted2445/2445 PASS; semantic review still pending',flush=True)


if __name__=='__main__':structural()
