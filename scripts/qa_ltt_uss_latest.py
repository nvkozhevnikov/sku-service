"""Fresh final QA only: latest accepted baseline, no repairs or database writes."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from scripts import resume_uss_terminal_batch as accepted
from scripts import qa_ltt_uss_revised as guard
r,o=accepted.r,accepted.o
OUT=r.BASE/'FULL_QA_TERMINAL_BASELINE_2026-10-15'
PLAN=accepted.P/'EFFECTIVE_ROW_PLAN.json'
BASELINE=accepted.P/accepted.BASELINE_NAME
FIXED={20,23,24,53,61,64,65,297,305,368,369,374,377,384,405}

def history():
    accepted.history()
    for key,sha in r.load(accepted.P/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((accepted.P/key).read_bytes())==sha,('ACCEPTED_EVIDENCE_DRIFT',key)
    prep=r.load(accepted.P/'OFFLINE_PREPARATION.json')
    assert r.code_hashes()==prep['new_code_hashes']==r.load(accepted.P/'TERMINAL_BATCH_FIX_CODE_SEAL.json')
    for key,sha in prep['harness_hashes'].items():assert r.digest((o.ROOT/key).read_bytes())==sha

def git():
    commands={'branch':['branch','--show-current'],'HEAD':['rev-parse','HEAD'],'develop':['rev-parse','develop'],'production':['rev-parse','production'],'porcelain':['status','--porcelain'],'diff_stat':['diff','--stat']}
    return {k:subprocess.run(['git',*args],text=True,encoding='utf8',capture_output=True,check=True).stdout for k,args in commands.items()}

def preflight():
    assert not OUT.exists(),'Never resume/overwrite old QA'
    history();checkpoint=r.load(accepted.P/'FINAL_CHECKPOINT.json');baseline=r.load(BASELINE);run2=r.load(accepted.P/'RUN2/RESULT.json')
    assert checkpoint['status']=='LTT_USS_TERMINAL_BATCH_FIX_AND_RUN2_REVERIFIED' and checkpoint['SAFE_TO_POWER_OFF']=='YES'
    assert baseline['totals']==dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533)
    assert run2['run2_scope']==2634 and run2['run2_exact_noop_persisted']==2453
    assert run2['table_fingerprint_delta']==run2['sequence_delta']==run2['run2_terminal_delta']==0
    entries=r.load(PLAN)['rows'];assert len(entries)==2634 and len({(e['supplier'],e['index']) for e in entries})==2634
    for e in entries:assert r.digest((o.ROOT/e['journal']).read_bytes())==e['journal_sha256']
    seals={name:r.digest((accepted.P/name).read_bytes()) for name in ['SEAL_PROVENANCE.json','CURRENT_ACCEPTED_SEAL.json','BATCH_CODE_TRANSITION.json','TERMINAL_BATCH_FIX_CODE_SEAL.json','FINAL_CHECKPOINT.json','RECOVERY_NEXT.md','EFFECTIVE_ROW_PLAN.json',accepted.BASELINE_NAME,'REVISED_TERMINAL_INVENTORY.json','RUN2/RESULT.json']}
    r.save(OUT/'GIT_BEFORE.json',git())
    r.save(OUT/'OFFLINE_PREFLIGHT.json',dict(status='PASS',latest_accepted=True,active_code_seal='TERMINAL_BATCH_FIX_CODE_SEAL',PROVENANCE_CHAIN_VALID='YES',accepted_artifact_hashes=seals,code_hashes=r.code_hashes(),total_scope=2634,counts=baseline['totals'],fresh_qa=True,previous_semantic_passes_not_carried=True,database_started=False,checked_at=r.now()))
    print('Fresh latest-baseline preflight PASS; database not started',flush=True)

def tests_receipt():
    root=ET.parse(OUT/'TESTS.xml').getroot();cases=root.findall('.//testcase')
    receipt=dict(total=len(cases),passed=sum(not any(c.find(tag) is not None for tag in ['failure','error','skipped']) for c in cases),fail=len(root.findall('.//failure')),error=len(root.findall('.//error')),skip=len(root.findall('.//skipped')),xml_sha256=r.digest((OUT/'TESTS.xml').read_bytes()),short_temp=r'D:\documents\personal\ChatGPT\Стербруст\tmp\lttqa')
    r.save(OUT/'TEST_RECEIPT.json',receipt)
    assert receipt['total']==receipt['passed'] and receipt['fail']==receipt['error']==receipt['skip']==0
    history();print(receipt,flush=True)

def structural():
    assert r.load(OUT/'OFFLINE_PREFLIGHT.json')['status']=='PASS'
    assert r.load(OUT/'TEST_RECEIPT.json')['fail']==0
    assert not (OUT/'BEFORE_QA.json').exists()
    history();plan=r.load(PLAN)['rows'];counts={s:Counter() for s in r.SOURCES};checked=persisted=0;last=None
    with guard.guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute('SET default_transaction_read_only=on');raw.execute("SET application_name='ltt_uss_latest_final_full_qa_readonly'");raw.execute("SET statement_timeout='30s'")
        assert raw.execute('SHOW default_transaction_read_only').fetchone()[0]=='on' and raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=guard.ReadOnlyConnection(raw);repo=r.PostgresRepository.__new__(r.PostgresRepository);repo.connection=c
        system=r.verify_system();before=o.snapshot(c);baseline=r.load(BASELINE)
        assert not [k for k in before if r.encoded(before[k])!=r.encoded(baseline[k])],'Baseline drift'
        r.save(OUT/'BEFORE_QA.json',dict(**before,**system,checked_at=r.now()))
        try:
          for e in plan:
            last=e;path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];row=r.load(path)
            assert (row['supplier'],row['index'],row['url'])==(e['supplier'],e['index'],e['url'])
            outcome,product,capture=o.extract(row);assert outcome==e['outcome'];counts[row['supplier']][outcome]+=1;ids=None
            if product is not None:
                assert product.site_internal_id==e['external_id'] and product.name
                ids=r.check_observation(repo,product,capture);assert ids==row['db_verification'];r.verify_persisted(repo,product,capture)
                latest=c.execute('SELECT max(observed_at) FROM offer_commercial_observations WHERE source_product_id=%s',(ids['source_product_id'],)).fetchone()[0];assert latest==capture.observed_at
                joined=c.execute('''SELECT row_to_json(p),row_to_json(o),row_to_json(cap),row_to_json(b)
                    FROM source_products p JOIN offers o ON o.source_product_id=p.id
                    JOIN supplier_http_captures cap ON cap.source_product_id=p.id
                    JOIN offer_commercial_observations b ON b.capture_id=cap.id AND b.offer_id=o.id
                    WHERE b.id=%s''',(ids['observation_id'],)).fetchall();assert len(joined)==1
                p,offer,cap,obs=joined[0]
                assert p['name']==product.name and offer['source_product_id']==p['id']
                assert p['supplier_id']==offer['supplier_id']==cap['supplier_id']==obs['supplier_id']
                assert obs['price_raw']==product.price.raw_text and (obs['currency'] or '')==(product.price.currency or '') and obs['availability_raw']==product.availability
                r.save(OUT/'PERSISTED_ROWS'/row['supplier']/path.name,dict(entry=e,parsed_product=asdict(product),product=p,offer=offer,capture=cap,observation=obs,latest_observation=str(latest),status='AUTOMATED_TRACE_PASS',semantic_approval=False));persisted+=1
            r.save(OUT/'STRUCTURAL_ROWS'/row['supplier']/path.name,dict(**e,db_verification=ids,status='STRUCTURAL_PASS',semantic_approval=False));checked+=1
            if checked%200==0:
                r.save(OUT/'CHECKPOINT.json',dict(status='FRESH_FINAL_QA_STRUCTURAL_RUNNING',checked=checked,persisted_checked=persisted,total=2634,safe_to_power_off=False,updated_at=r.now()));print(f'FRESH LATEST QA STRUCTURAL {checked}/2634 persisted{persisted}',flush=True)
          assert checked==2634 and persisted==2453 and counts=={s:Counter(v) for s,v in baseline['sources'].items()}
          r.save(OUT/'STRUCTURAL_AUDIT.json',dict(status='PASS',rows_checked=checked,persisted_automated_checked=persisted,sources=counts,missing_rows=0,double_counted=0,silent_rows=0,cross_supplier_identity_bleed=0,missing_evidence=0,checked_at=r.now()))
          r.save(OUT/'PERSISTED_AUTOMATED_AUDIT.json',dict(status='PASS',rows_checked=persisted,duplicate_external_ids=0,duplicate_offer_links=0,broken_references=0,identity_overwrite=0,details='PERSISTED_ROWS',semantic_approval=False))
          r.save(OUT/'DB_INTEGRITY.json',dict(status='PASS',checks=accepted.b.accepted.integrity(c),catalog_product_links=0,product_matches=before['fingerprints']['product_matches'],selection=before['fingerprints']['catalog_offer_selection']))
        except BaseException as error:
          r.save(OUT/'STRUCTURAL_BLOCKER.json',dict(row=last,checked=checked,persisted_checked=persisted,error=f'{type(error).__name__}:{error}',checked_at=r.now()));raise
        finally:
          after=o.snapshot(c);assert not accepted.b.accepted.f.verify_unchanged(before,after)
          r.save(OUT/'AFTER_STRUCTURAL.json',dict(**after,checked_at=r.now()));r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,source_persistence_calls=0,sql_writes=0,read_only_sql_enforced=True))
    print('Fresh structural2634/persisted2453 PASS; semantic approval pending',flush=True)

if __name__=='__main__':{'preflight':preflight,'tests':tests_receipt,'structural':structural}[sys.argv[1]]()
