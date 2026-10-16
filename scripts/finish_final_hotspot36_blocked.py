"""Offline terminal receipt only; no SQL or further semantic review."""
from scripts.final_hotspot36 import *
from scripts import final_hotspot36_metadata_correction as correction
from scripts import final_hotspot36_seals as seals
import json

def main():
    correction.validate_gate();seals.verify(baseline_seal=seals.ACCEPTED,proposed_transition=seals.PROPOSED)
    F=S/'FRESH_FULL_QA';review=r.load(F/'SEMANTIC_REVIEW_PROGRESS.json');block=r.load(F/'MATERIAL_BLOCKER_MSK171_EDITORIAL.json')
    assert review['reviewed']==2 and review['passed']==review['failed']==1 and review['unreviewed']==398
    assert (block['supplier'],block['row'],block['stable_id'])==('ltt_msk',171,'1723')
    shutdown=json.loads((S/'SHUTDOWN.json').read_text('utf-8-sig'));assert shutdown['SAFE_TO_POWER_OFF']=='YES' and shutdown['status']=='CLEAN_SERVER_STOPPED'
    sql=r.load(S/'PRE_SHUTDOWN_SQL.json');assert sql['current_db_baseline_intact'] and sql['table_fingerprint_delta']==sql['sequence_delta']==sql['other_clients']==sql['open_transactions']==sql['prepared_transactions']==0
    receipt=r.load(S/'HOTSPOT36_CORRECTION_RECEIPT.json');assert receipt['committed'] and receipt['fresh_committed_pairs_verified']==36 and len(receipt['rows'])==36
    assert r.load(S/'IDEMPOTENCY_RECEIPT.json')['changed_rows']==0 and r.load(S/'POST_QA_CORRECTED36_FRESH_SQL.json')['verified_pairs']==36
    run=r.load(S/'RUN2/RESULT.json');assert run['status']=='PASS' and run['scope']==2634 and run['exact_noop_persisted']==2453
    for label,total in [('HOTSPOT36',36),('DISK10',10),('TRUE_DISK137',137),('INVERSE97',97),('PREVIOUS29',29),('VALID_MACHINE362',362),('OPERATOR106',106)]:
        mapping=r.load(F/(label+'_MAPPING.json'));assert mapping['status']=='PASS' and mapping['count']==len(mapping['rows'])==total
    snapshot=r.load(S/'AFTER_ALL_VERIFICATION.json')
    existing_ids={p['external_id'] for p,v in snapshot['product_offer_projections']}
    existing_urls={u.removesuffix('index.htm').rstrip('/') for p,v in snapshot['product_offer_projections'] for u in [p['source_url'],p['canonical_url']] if u}
    checked=0
    for name,total in [('AMBIGUOUS_DECISION_VERIFICATION.json',114),('MULTI_VARIANT_DECISION_VERIFICATION.json',66)]:
        mapping=r.load(F/name);assert mapping['mapping_status']=='PASS' and mapping['count']==total
        for row in mapping['rows']:
            assert row['source_id'] not in existing_ids and row['url'].removesuffix('index.htm').rstrip('/') not in existing_urls
            checked+=1
    assert checked==180
    r.save(F/'TERMINAL_ABSENCE_FROM_CLOSEOUT_SNAPSHOT.json',dict(status='PASS',terminal_pages=180,source_entities_found=0,snapshot_sha256=r.digest((S/'AFTER_ALL_VERIFICATION.json').read_bytes()),evidence_class='LIVE-VERIFIED_CURRENT_CLOSEOUT_SQL_SNAPSHOT',semantic_approval=False))
    before=r.load(S/'GIT_BEFORE.json');after=q.git()
    for k in ['branch','HEAD','develop','production']:assert before[k]==after[k],k
    r.save(S/'GIT_FINAL.json',after)
    selection=r.load(F/'FRESH_400_SAMPLE_MANIFEST.json');reviewed={(a['supplier'],a['index']):a for a in review['rows']}
    for e in selection['rows']:
        a=reviewed.get((e['supplier'],e['index']));e['semantic_status']=a['decision'] if a else 'NOT_REVIEWED'
        if a:
            assert r.digest((F/a['path']).read_bytes())==a['sha256'];e.update(semantic_facts=a['path'],semantic_facts_sha256=a['sha256'])
    selection.update(status='BLOCKED',reviewed=2,passed=1,failed=1,unreviewed=398,by_source=review['by_source']);r.save(F/'FRESH_400_SAMPLE_MANIFEST.json',selection)
    tests=r.load(S/'TEST_RECEIPT.json');assert tests['passed']==4007 and tests['fail']==tests['error']==tests['skip']==0
    cp=dict(status='LTT_USS_SOURCE_INTEGRATION_FINAL_BLOCKED',STAGE='FRESH_FULL_QA_SEMANTIC_PAGE_ROLE',SOURCE='ltt_msk',ROW=171,SOURCE_ID='1723',SOURCE_PRODUCT_ID=227,OFFER_ID=227,BLOCKER='Editorial article comparing two CNC executions persisted as one source product/offer by miniShop template gate',EXPECTED='Fail-closed non-product editorial eligibility; retain unknown/UNKNOWN diagnostic and do not invent one offered machine',ACTUAL='PERSISTED source_product227/offer227,kindunknown/UNKNOWN,templatePOR/InStock',CONFIRMED_SCOPE=1,WIDER_SCOPE='NOT_ASSESSED_STOPPED_AT_FIRST_NEW_MATERIAL_QA_DEFECT',
      DONE='Generic exact36 fix, full2634 impact, tests4007+8, paired CAS36, actual RUN2 and fresh structural QA',APPLIED='Exactly36 source_product+36offer product_kind pairs committed; no editorial eligibility repair or deletion',VERIFIED='Authorized36 code/source tests/SQL/idempotency and RUN2/structural/protected technical mappings. FULL_QA_NOT_VERIFIED',HOTSPOT36_CORRECTION_APPLIED='YES_36_SOURCE_36_OFFER',FINAL_HOTSPOT_FIXED=36,POWER_FEEDER_FIXED=26,KNIFE_FIXED=4,SPARE_FIXED=3,FIXTURE_FIXED=1,CHILLER_FIXED=2,CORRECTED_BY_SUPPLIER=dict(ltt_msk=19,ltt_sib=7,ltt_yug=10,uss_stanko=0),IDEMPOTENCY_CHANGED_ROWS=0,PRIOR29_REPEATED=False,INVERSE97_REPEATED=False,DISK10_REPEATED=False,
      RUN2_COMPLETED=True,RUN2_SCOPE=2634,RUN2_EXACT_NOOP=2453,RUN2_NEW_ENTITIES=0,RUN2_TABLE_FINGERPRINT_DELTA=0,RUN2_SEQUENCE_DELTA=0,FULL_QA_STARTED=True,FULL_QA_COMPLETED=False,FULL_QA_VERIFIED=False,FULL_QA_PROGRESS=dict(structural='2634/2634 PASS',persisted='2453/2453 PASS',new_sample=400,sample_reviewed=2,sample_pass=1,sample_fail=1,sample_unreviewed=398,by_source=dict(ltt_msk=2,ltt_sib=0,ltt_yug=0,uss_stanko=0),fresh_mandatory_semantic_cohorts='Not completed at stop; no previous-phase passes reused',fresh_mapping=dict(hotspot36=36,disk10=10,true_disk137=137,inverse97=97,previous29=29,valid_machine362=362,operator106=106,uss_ambiguous=114,uss_multi=66),operator6_semantic='PENDING',risk9_semantic='PENDING',historical_uss15_semantic='PENDING',sib_units8_semantic='PENDING',terminal1_4_5_semantic='PENDING',yug_id_currency_semantic='PENDING',facts_prepared_before_manual_blocker=25),
      TESTS_PASS=4007,ADDITIONAL_SUBTESTS_PASS=8,TEST_FAIL=0,TEST_ERROR=0,TEST_SKIP=0,CURRENT_DB_BASELINE=str((S/'HOTSPOT36_REVISED_BASELINE.json').relative_to(o.ROOT)),CURRENT_DB_BASELINE_SHA256=r.digest((S/'HOTSPOT36_REVISED_BASELINE.json').read_bytes()),CURRENT_DB_BASELINE_INTACT=True,TABLE_FINGERPRINT_DELTA_DURING_QA=0,SEQUENCE_DELTA_DURING_QA=0,TABLES=51,SEQUENCES=40,SOURCE_PRODUCTS=2453,OFFERS=2453,CAPTURES=2533,OBSERVATIONS=2533,DB_WRITES_DURING_QA=0,SUPPLIER_HTTP=0,SQL_CLIENTS=0,OPEN_TRANSACTIONS=0,PREPARED_TRANSACTIONS=0,WORKERS=0,PG55455_RUNNING=False,POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT55455_LISTENING=False,POSTMASTER_PID_PRESENT=False,SAFE_TO_POWER_OFF='YES',FINALIZER_STARTED=False,GIT_PUBLICATION=False,PRODUCTION_WRITES=0,STAGE6='BLOCKED_IMPORT_CONTRACT_EVIDENCE',NEXT='OPERATOR_REVIEW_AND_SEPARATELY_AUTHORIZE_LTT_EDITORIAL_PAGE_ROLE_IMPACT; DO_NOT_REPEAT36_10_97_29',STOP=True,checked_at=r.now())
    for p in [S/'FINAL_CHECKPOINT.json',S/'CHECKPOINT.json',F/'CHECKPOINT.json',r.BASE/'CHECKPOINT.json']:r.save(p,cp)
    r.save(F/'FINAL_QA_RESULT.json',dict(status='BLOCKED',structural_rows=2634,persisted_rows=2453,reviewed=2,passed=1,failed=1,unreviewed=398,main_ledger='SEMANTIC_REVIEW_PROGRESS.json',blocker='MATERIAL_BLOCKER_MSK171_EDITORIAL.json',mandatory_semantic_cohorts_incomplete=True,prior_semantic_passes_used=False,full_qa_verified=False))
    print('BLOCKED:36APPLIED/RUN2VERIFIED/newQA2/400=1PASS1FAIL; baselineintact; SAFE YES',flush=True)

if __name__=='__main__':main()
