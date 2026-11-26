"""Current protected-output mapping and future regression risks; no patch."""
from scripts.review_pre_final_hotspots import *

def main():
    assert not (H/'HOTSPOT_PROTECTED_COHORTS.json').exists()
    allrows={key(a):a for a in r.load(H/'ALL_CURRENT_ROWS.json')['rows']}
    defects={key(a):a for a in r.load(H/'HOTSPOT_CONFIRMED_DEFECTS.json')['rows']}
    candidates={key(a) for group in ['FEEDER_FAMILY','TABLE_STEM','CONSUMABLE_CATEGORY'] for a in r.load(H/(group+'_INVENTORY.json'))['rows']}
    seal=r.load(S/'FINAL_ARTIFACT_SEAL.json');cohorts={}
    def accepted(path):
        assert r.digest(path.read_bytes())==seal[str(path.relative_to(S))];return r.load(path)
    for label,total in [('DISK10',10),('TRUE_DISK137',137),('INVERSE97',97),('PREVIOUS29',29),('VALID_MACHINE362',362),('OPERATOR106',106)]:
        saved=accepted(F/(label+'_MAPPING.json'));rows=[]
        for original in saved['rows']:
            e=original['entry'];a=allrows[e['supplier'],e['index']]
            assert a['entry']==e and a['current_kind']==original['current_kind'] and a['evidence_sha256']==original['evidence_sha256']
            assert a['db_evidence']['source_product_id']==original['source_product_id'] and a['db_evidence']['offer_id']==original['offer_id']
            rows.append(dict(entry=e,current_kind=a['current_kind'],payload_sha256=a['payload_sha256'],candidate_intersection=key(a) in candidates,confirmed_defect_overlap=key(a) in defects,current_baseline_unchanged=True))
        assert len(rows)==total
        cohorts[label]=dict(count=total,candidate_intersections=sum(a['candidate_intersection'] for a in rows),confirmed_defect_overlap=sum(a['confirmed_defect_overlap'] for a in rows),rows=rows,assessment='Current exact outputs and saved mappings preserved; future generic fix must prove exact regression, not verified by this audit')
    for label,indices,expected in [('TERMINAL_DISK1',[225],'unknown'),('TERMINAL_INVERSE4',[50,51,182,221],'machine'),('PREVIOUS_TERMINAL5',[191,194,195,196,197],'cutting_tool')]:
        rows=[]
        for index in indices:
            a=allrows['uss_stanko',index];assert not a['persisted'] and a['current_kind']['product_kind']==expected
            rows.append(dict(entry=a['entry'],current_kind=a['current_kind'],payload_sha256=a['payload_sha256'],candidate_intersection=key(a) in candidates,current_baseline_unchanged=True))
        cohorts[label]=dict(count=len(rows),rows=rows,candidate_intersections=sum(a['candidate_intersection'] for a in rows),confirmed_defect_overlap=0,assessment='Current terminal kind and ineligibility unchanged; no proposed correction')
    for label,total,name in [('USS_AMBIGUOUS114',114,'AMBIGUOUS_DECISION_VERIFICATION.json'),('USS_MULTI66',66,'MULTI_VARIANT_DECISION_VERIFICATION.json')]:
        saved=accepted(F/name);rows=[]
        for original in saved['rows']:
            a=allrows['uss_stanko',original['index']]
            # Terminal journals intentionally carry no entity ID. The old
            # mapping's source_id is a URL-derived audit key, not an entity.
            audit_key='uss:'+a['entry']['url'].split('.com/')[1].removesuffix('index.htm').rstrip('/')
            assert a['entry']['external_id'] is None and audit_key==original['source_id'] and a['entry']['url']==original['url'] and a['outcome']==original['current_outcome'] and a['evidence_sha256']==original['evidence_sha256'] and a['response_sha256']==original['response_sha256'] and not a['persisted']
            rows.append(dict(entry=a['entry'],terminal_audit_key=audit_key,source_entity_id=None,current_kind=a['current_kind'],payload_sha256=a['payload_sha256'],candidate_intersection=key(a) in candidates,current_mapping_unchanged=True))
        assert len(rows)==total
        cohorts[label]=dict(count=total,rows=rows,candidate_intersections=sum(a['candidate_intersection'] for a in rows),confirmed_defect_overlap=0,assessment='All current114/66 outcome and evidence mappings preserved; not a new full semantic review')
    for label,keys in [('VALID_FEEDER2',TRUE_FEEDER),('TRUE_SPARE_FOR_FEEDER1',TRUE_SPARE),('VALID_TABLE9',TRUE_TABLE),('VALID_CONSUMABLE2',TRUE_CONSUMABLE)]:
        cohorts[label]=dict(count=len(keys),rows=[dict(entry=allrows[k]['entry'],current_kind=allrows[k]['current_kind'],payload_sha256=allrows[k]['payload_sha256']) for k in sorted(keys)],assessment='Own source semantic review completed in this audit; future fix must retain exact current payload/outcome')
    parts=[]
    pattern=r'^(?:ролик\w*|колес\w*|валец\w*|втулк\w*|(?:подающ\w*|прижимн\w*|опорн\w*|приводн\w*|резинов\w*|полиуретанов\w*|нижн\w*|верхн\w*)\s+(?:ролик\w*|колес\w*|валец\w*|втулк\w*))\b'
    for k,a in allrows.items():
        if k in defects or a['current_kind']['product_kind']!='spare':continue
        if re.search(pattern,normalize_text(a['title'])):parts.append(dict(entry=a['entry'],title=a['title'],current_kind=a['current_kind'],payload_sha256=a['payload_sha256'],role='OWN_COMPONENT_NOUN_RISK_WITNESS_NOT_NEW_SEMANTIC_APPROVAL'))
    assert ('ltt_msk',298) in {key(a) for a in parts} and ('ltt_msk',330) in {key(a) for a in parts}
    cohorts['ROLLER_WHEEL_BUSHING_COMPONENT_RISK']=dict(count=len(parts),selector=pattern,rows=parts,assessment='Extra current spare outputs with component heads. Risk inventory only; no broader new repair/semantic acceptance. Do not globally reinterpret feeder/roller words or exempt every prior kind.')
    assert not any(c.get('confirmed_defect_overlap',0) for c in cohorts.values())
    r.save(H/'HOTSPOT_PROTECTED_COHORTS.json',dict(status='ASSESSED_NOT_IMPLEMENTED',cohorts=cohorts,all_current_outputs_equal_accepted=True,business_code_changed=False,fix_implemented=False,future_fix_regression_verified=False,source_writes=0,db_writes=0,terminal_identity_note='Terminal journal entity IDs remain None. Historical source_id is URL-derived mapping audit key; exact URL/raw/body/outcome mappings compared without fabricating a source entity.'))
    r.save(H/'FUTURE_BATCH_CONCEPT.json',dict(status='CONCEPT_ONLY_NO_AUTHORIZATION_TO_APPLY',confirmed_persisted_scope=36,concepts=[
      'Recognize complete feeder from own functional head/purpose even when prior taxonomy is unknown; do not classify a roller FOR a feeder as the feeder.',
      'Resolve subordinate roller counts/adjectives by sold-object role before adapter spare override; retain actual rollers/feed wheels/replacement components and all accepted complete-machine handling.',
      'Separate woodworking adjective столярный from table noun morphology; still recognize genuine стол-верстак and столярные верстаки, including terminal HB workbench family.',
      'Prefer source-proved knife, bearing-guide replacement assembly or clamp over mixed host category. Use existing knife, ingestion spare and fixture; no new one-product subtype.',
      'Recognize own complete chiller before mixed consumables fallback without reclassifying replacement CO2 mirrors; preserve existing29 chiller and all prices/stocks.',
      'Propose one generic batch only after operator review; require exact36 allowlist, full2634 input/output impact and protected cohorts, meaningful tests and explicit code-seal transition before any authorized metadata CAS.',
      'Future persisted repair must preserve source identity/title/commercial payload/outcomes, use durable rollback/accepted CAS, verify idempotency0, then actual normal-session guarded RUN2 and brand-new QA. Do not resume old24/400 or infer future QA PASS from this sweep.'
    ],confidence='No expected HIGH forced by this audit; a future implementation must emit supported confidence',existing_spare_note='spare is established source-ingestion adapter kind, absent from core registry; use distinction honestly',boundaries=['No current code/DB/source repair','No matching/reconciliation/selection/finalizer/publication/production','Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE']))
    print('PROTECTED risk assessed', {k:v['count'] for k,v in cohorts.items()},flush=True)

if __name__=='__main__':main()
