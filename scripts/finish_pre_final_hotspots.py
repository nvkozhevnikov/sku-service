"""Offline verified hotspot-sweep checkpoint. Requires finished runtime receipt."""
from scripts.review_pre_final_hotspots import *
from scripts import disk_alias_metadata_correction as correction
import json

def main():
    assert not (H/'FINAL_CHECKPOINT.json').exists();correction.validate_gate()
    pre=r.load(H/'PREFLIGHT.json')
    for name,sha in pre['gated_files'].items():assert r.digest((o.ROOT/name).read_bytes())==sha,name
    for name,sha in pre['consumed_file_seal'].items():assert r.digest((S/name).read_bytes())==sha,name
    before=r.load(H/'GIT_BEFORE.json');after=q.git()
    for k in ['branch','HEAD','develop','production']:assert before[k]==after[k]
    r.save(H/'GIT_FINAL.json',after)
    runtime=json.loads((H/'RUNTIME_FINAL.json').read_text('utf-8-sig'));assert runtime['SAFE_TO_POWER_OFF']=='YES' and runtime['workers_remaining']==0 and not runtime['port55455_listening'] and not runtime['postmaster_pid_present']
    allrows=r.load(H/'ALL_CURRENT_ROWS.json');assert allrows['counts']==dict(persisted=2453,terminal=180) and allrows['current_payload_delta']==allrows['terminal_outcome_delta']==0
    decisions={}
    for group,name in [('FEEDER_FAMILY','FEEDER_SEMANTIC_DECISIONS'),('TABLE_STEM','TABLE_STEM_SEMANTIC_DECISIONS'),('CONSUMABLE_CATEGORY','CONSUMABLE_SEMANTIC_DECISIONS')]:
      inventory=r.load(H/(group+'_INVENTORY.json'));d=r.load(H/(name+'.json'));assert {key(a) for a in inventory['rows']}=={key(a) for a in d['rows']} and d['unreviewed']==0 and d['reviewed']==inventory['count']
      for a in d['rows']:
        assert a['semantic_decision'] and r.digest((H/a['source_packet']).read_bytes())==a['source_packet_sha256']
        if a['persisted']:assert r.digest((o.ROOT/a['db_evidence']['record']).read_bytes())==a['db_evidence']['record_sha256']
      decisions[group]=d
    defects=r.load(H/'HOTSPOT_CONFIRMED_DEFECTS.json');assert len({key(a) for a in defects['rows']})==defects['count']==36 and all(a['persisted'] for a in defects['rows'])
    assert any(key(a)==('ltt_msk',176) and a['stable_id']=='1770' for a in defects['rows'])
    protected=r.load(H/'HOTSPOT_PROTECTED_COHORTS.json');assert protected['all_current_outputs_equal_accepted'] and not protected['future_fix_regression_verified']
    cp=dict(status='LTT_USS_PRE_FINAL_HOTSPOT_SWEEP_VERIFIED',DONE='Full2634 current-code replay and50 own-source candidate decisions with exact confirmed scope',APPLIED='Audit helpers/artifacts/documents only; no business or persisted correction',VERIFIED='Scoped offline inventory, rule traces, source interpretation ledger and accepted baseline/protected mappings; not full integration QA',FEEDER_CANDIDATES=29,FEEDER_REVIEWED=29,FEEDER_FALSE_SPARE=12,FEEDER_UNKNOWN_COMPLETE=14,FEEDER_VALID_SPARE=1,FEEDER_VALID_POWER_FEEDER=2,FEEDER_OPERATOR_REVIEW=0,FEEDER_TERMINAL=0,MSK176_CONFIRMED='YES',TABLE_CANDIDATES=17,TABLE_PERSISTED_CANDIDATES=15,TABLE_TERMINAL_CANDIDATES=2,TABLE_REVIEWED=17,FALSE_TABLE=8,VALID_TABLE=9,VALID_PERSISTED_TABLE=7,VALID_TERMINAL_TABLE=2,TABLE_OPERATOR_REVIEW=0,CONSUMABLE_CANDIDATES=4,CONSUMABLE_REVIEWED=4,FALSE_CONSUMABLE=2,VALID_CONSUMABLE=2,CONSUMABLE_OPERATOR_REVIEW=0,TOTAL_CANDIDATES=50,TOTAL_PERSISTED_CANDIDATES=48,TOTAL_TERMINAL_CANDIDATES=2,UNREVIEWED=0,TOTAL_CONFIRMED_PERSISTED_DEFECTS=36,TOTAL_CONFIRMED_TERMINAL_DEFECTS=0,AFFECTED_SUPPLIERS=['ltt_msk','ltt_sib','ltt_yug'],AFFECTED_SUPPLIER_COUNTS=defects['by_supplier'],TARGET_COUNTS=defects['target_counts'],ROOT_CAUSE_CLUSTERS=4,REPLAY_SCOPE=2634,PERSISTED_REPLAY=2453,TERMINAL_REPLAY=180,CATEGORY_SEPARATE=1,CURRENT_PAYLOAD_DELTA=0,TERMINAL_OUTCOME_DELTA=0,DB_WRITES=0,SQL_CONNECTIONS=0,SOURCE_WRITES=0,BUSINESS_CODE_CHANGES=0,SUPPLIER_HTTP=0,DB_STARTED=False,DB_BASELINE_SOURCE='PACKAGED-EVIDENCE_LAST_VERIFIED_SQL; not a fresh SQL verification',CURRENT_ACCEPTED_BASELINE_EVIDENCE_INTACT=True,FULL_QA_RESUMED=False,FULL_QA_VERIFIED='NO',PRIOR_FULL_QA_PROGRESS='24/400 remains blocked and untouched',FINALIZER_STARTED='NO',MATCHING_OR_SELECTION_STARTED=False,GIT_PUBLICATION=False,PRODUCTION_WRITES=0,STAGE6='BLOCKED_IMPORT_CONTRACT_EVIDENCE',SAFE_TO_POWER_OFF='YES',NEXT='OPERATOR_REVIEW_AND_SINGLE_FINAL_HOTSPOT_BATCH',STOP=True,checked_at=r.now())
    for path in [H/'FINAL_CHECKPOINT.json',H/'CHECKPOINT.json',r.BASE/'CHECKPOINT.json']:r.save(path,cp)
    (H/'RECOVERY_NEXT.md').write_text('# Recovery / operator review\n\nLTT_USS_PRE_FINAL_HOTSPOT_SWEEP_VERIFIED / STOP.\n\nExact36 persisted defects: MSK19/SIB7/YUG10, targets power_feeder26/knife4/spare3/fixture1/chiller2. All50 candidates reviewed; terminal2 valid tables, no terminal correction. See inventories, semantic decisions, confirmed defects and4 actual rule clusters. Full2634 replay identical to accepted current disk10 baseline.\n\nNo business/source/DB change, SQL connection or supplier HTTP; PG55455 remained stopped, SAFEYES. FullQA24/400 untouched and NOTVERIFIED. Future generic fix is concept only and requires operator review/separate authorization; do not repeat10/29/97. Preserve all protected cohorts, true feeder rollers, genuine tables/workbenches and mirrors. Do not treat this sweep as implementation regression QA.\n\nNEXT OPERATOR_REVIEW_AND_SINGLE_FINAL_HOTSPOT_BATCH. No finalizer/publication/production/Stage7/55452. STOP.\n',encoding='utf-8')
    r.save(H/'NO_MUTATION_RECEIPT.json',dict(status='PASS',accepted_gated_code_unchanged=True,consumed_accepted_evidence_unchanged=True,git_refs_unchanged=True,db_writes=0,sql_connections=0,business_source_writes=0,supplier_http=0,full_qa_resumed=False))
    print('HOTSPOT SWEEP VERIFIED50/50;36persisteddefects;4clusters;0unreviewed;no mutation;SAFE YES',flush=True)

if __name__=='__main__':main()
