"""Offline terminal documentation only; never connect or repair."""
from scripts.disk_alias_batch import *
from scripts.execute_disk_alias10 import history
from scripts import disk_alias_metadata_correction as correction
import json

history();correction.validate_gate()
for n,sha in r.load(S/'ARCHIVED_ACCEPTED_FILE_SEAL.json').items():
    assert r.digest((S/'ARCHIVED_ACCEPTED_CODE'/n).read_bytes())==sha,n
before=r.load(S/'GIT_BEFORE.json');after=q.git()
for key in ['branch','HEAD','develop','production']:assert before[key]==after[key],key
r.save(S/'GIT_FINAL.json',after)
shutdown=json.loads((S/'SHUTDOWN.json').read_text('utf-8-sig'))
assert shutdown['SAFE_TO_POWER_OFF']=='YES' and shutdown['status']=='CLEAN_SERVER_STOPPED'
proof=r.load(S/'PRE_SHUTDOWN_SQL.json')
assert proof['other_clients']==proof['open_transactions']==proof['prepared_transactions']==0
assert proof['current_db_baseline_intact'] and proof['table_fingerprint_delta']==proof['sequence_delta']==0
F=S/'FRESH_FULL_QA'
cp=dict(status='LTT_USS_SOURCE_INTEGRATION_FINAL_BLOCKED',STAGE='FRESH_FULL_QA_SEMANTIC',SOURCE='ltt_msk',ROW=176,SOURCE_ID='1770',SOURCE_PRODUCT_ID=232,OFFER_ID=232,
 BLOCKER='Functional automatic feeder classified spare/HIGH because title contains subordinate 8 роликов',EXPECTED='power_feeder sold object supported by own purpose; no repair applied',ACTUAL='spare/HIGH/source=sold_title',CONFIRMED_SCOPE=1,WIDER_SCOPE='NOT_ASSESSED_STOPPED_AT_FIRST_NEW_MATERIAL_DEFECT',
 DONE='Generic disk-alias fix, all-row impact, tests, exact10 CAS, RUN2 and fresh structural QA',APPLIED='Exactly10 source_product+offer nested kind pairs committed; terminal USS225 computed unknown; no MSK176 repair',VERIFIED='Current disk10 pairs, frozen full impact, test gates, RUN2, structural/protected mappings and named fresh cohorts; FULL_QA_NOT_VERIFIED',
 DISK10_CORRECTION_APPLIED='YES_10_SOURCE_10_OFFER',IDEMPOTENCY_CHANGED_ROWS=0,TERMINAL1_COMPUTED_CORRECTED=True,INVERSE97_REPEATED=False,PRIOR29_REPEATED=False,RUN2_COMPLETED=True,RUN2_SCOPE=2634,RUN2_DURABLE_ROWS=2634,RUN2_EXACT_NOOP=2453,RUN2_NEW_ENTITIES=0,FULL_QA_STARTED=True,FULL_QA_COMPLETED=False,FULL_QA_VERIFIED=False,
 FULL_QA_PROGRESS=dict(structural=2634,persisted=2453,new_sample=400,main_sample_reviewed=24,main_sample_pass=23,main_sample_fail=1,main_sample_unreviewed=376,main_reviewed_per_source=dict(ltt_msk=24,ltt_sib=0,ltt_yug=0,uss_stanko=0),separate_fresh_cohorts=dict(disk10='10/10 PASS',true_disk137='137/137 PASS',inverse97='97/97 PASS',previous29='29/29 PASS',terminal1='1/1 PASS',terminal4='4/4 PASS',terminal5='5/5 PASS',operator6='6/6 PASS',multi_risk9='9/9 PASS'),cohort_counts_not_added_to_main_sample=True,mandatory_incomplete=['USS historical15 fresh semantic group','SIB8 unit-price fresh semantic group','YUG stable312/297 and USD pair fresh semantic group','Remaining fresh400 rows']),
 TESTS_PASS=2971,ADDITIONAL_SUBTESTS_PASS=8,TEST_FAIL=0,TEST_ERROR=0,TEST_SKIP=0,CURRENT_DB_BASELINE=str((S/'DISK_ALIAS_REVISED_BASELINE.json').relative_to(o.ROOT)),CURRENT_DB_BASELINE_SHA256=r.digest((S/'DISK_ALIAS_REVISED_BASELINE.json').read_bytes()),CURRENT_DB_BASELINE_INTACT=True,TABLE_FINGERPRINT_DELTA_DURING_RUN2=0,TABLE_FINGERPRINT_DELTA_DURING_QA=0,SEQUENCE_DELTA=0,TABLES=51,SEQUENCES=40,SOURCE_PRODUCTS=2453,OFFERS=2453,CAPTURES=2533,OBSERVATIONS=2533,SUPPLIER_HTTP=0,SQL_CLIENTS=0,OPEN_TRANSACTIONS=0,PREPARED_TRANSACTIONS=0,WORKERS=0,PG55455_RUNNING=False,POSTGRES_55455_SHUTDOWN='CLEAN_SERVER_STOPPED',PORT55455_LISTENING=False,POSTMASTER_PID_PRESENT=False,SAFE_TO_POWER_OFF='YES',FINALIZER_STARTED=False,GIT_PUBLICATION=False,PRODUCTION_WRITES=0,STAGE6='BLOCKED_IMPORT_CONTRACT_EVIDENCE',NEXT='OPERATOR_REVIEW_AND_SEPARATELY_AUTHORIZE_SCOPED_FEEDER_IMPACT; DO_NOT_REPEAT_DISK10_OR_29_OR_97',STOP=True,checked_at=r.now())
for path in [S/'CHECKPOINT.json',S/'FINAL_CHECKPOINT.json',F/'CHECKPOINT.json',r.BASE/'CHECKPOINT.json']:r.save(path,cp)
r.save(F/'FINAL_QA_RESULT.json',dict(status='BLOCKED',full_qa_verified=False,prior_passes_used=False,structural_rows=2634,persisted_rows=2453,main_sample_reviewed=24,main_sample_pass=23,main_sample_fail=1,blocker='MATERIAL_BLOCKER_MSK176.json',checkpoint='CHECKPOINT.json',safe_to_power_off=True))
r.save(S/'FINAL_CODE_AND_HISTORY_VERIFICATION.json',dict(status='PASS',historical_phase_seals_unchanged=True,archived_files_unchanged=True,current_proposed_code_gate_unchanged=True,git_refs_unchanged=True,checked_at=r.now()))
print('Final code/history/archive/refs PASS; current disk10 baseline intact; BLOCKED checkpoint durable; SAFE YES',flush=True)
