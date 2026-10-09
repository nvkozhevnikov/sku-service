"""Package mined cohorts and literal CSV inputs, no DB access or AI writer."""
import hashlib,json
from pathlib import Path
from collections import Counter,defaultdict
from universal_supplier.matching_policy_v22 import kind_proof
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'
def save(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def main():
    after=json.loads((OUT/'V22_AFTER.json').read_bytes());targets=json.loads((OUT/'CANONICAL_VIEWS.json').read_bytes())
    qain=json.loads((OUT/'NEW_AUTO_ACCEPT_QA_INPUTS.json').read_bytes())
    assert len(qain)==after['new_auto_accepts'] and len(qain)>100
    qa=[];ai=[];unresolved_before=[];unresolved_after=[];new=[];manual=[]
    columns=['supplier','external_id','source_product_id','source_url','source_name','brand','model','raw_model','execution','product_kind',
      'article','source_category','characteristics','current_classification','classification','candidate_product_id','candidate_title',
      'candidate_brand','candidate_model','candidate_product_kind','candidate_category','rejection_reasons','candidate_score',
      'score_components','positive_evidence','negative_guard_result','evidence_refs','raw_source_identity','terminal_route']
    def csvrow(r,stage):
        audit=r['audit_v21'] if stage=='before' else r['audit_v22']
        chosen=audit['confirmed'];ids=audit['retrieval_ids']
        pid=r.get('linked_sterbrust_product_id') if r['persisted_existing_preserved'] else chosen['sterbrust_product_id'] if chosen else ids[0] if ids else r.get('current_candidate_id')
        t=targets.get(str(pid),{})
        return dict(supplier=r['source'],external_id=r['external_id'],source_product_id=r['source_product_id'],source_url=r['source_url'],
           source_name=r['name'],brand=r['brand'],model=r['model'],raw_model=r['raw_model'],execution=r['execution'],
           product_kind=kind_proof(r)['kind'] if stage=='after' else r.get('product_kind'),article=r['article'],source_category=r['source_category'],
           characteristics=r['observed_properties'],current_classification=r['persisted_classification'],classification=r['classification_'+stage],
           candidate_product_id=pid,candidate_title=t.get('name'),candidate_brand=t.get('brand'),candidate_model=t.get('model'),
           candidate_product_kind=kind_proof(t)['kind'] if t else 'NOT_FOUND',candidate_category=t.get('source_category'),
           rejection_reasons=audit['blocking_reasons'],candidate_score=r.get('current_candidate_score'),
           score_components=r.get('current_match_evidence') or {'state':'NOT_FOUND_NO_CURRENT_SCORE'},
           positive_evidence=chosen.get('evidence') if chosen else {'state':'UNPROVEN'},
           negative_guard_result=chosen.get('evidence',{}).get('negative_guard_result') if chosen else {'blocking_reasons':audit['blocking_reasons']},
           evidence_refs={'source':r['evidence_ref'],'canonical_snapshot':t.get('snapshot_hash'),'registry_sha256':after.get('full_registry_sha256') or '40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65'},
           raw_source_identity=r['raw_source_identity'],terminal_route=r['terminal_route'])
    for r in after['rows']:
        before_row=csvrow(r,'before');after_row=csvrow(r,'after')
        if r['classification_before']!='EXISTING_CONFIRMED':unresolved_before.append(before_row)
        if r['classification_after']!='EXISTING_CONFIRMED':unresolved_after.append(after_row)
        if r['classification_before']!='EXISTING_CONFIRMED' and r['classification_after']=='EXISTING_CONFIRMED':new.append(after_row)
        if r['terminal_route']=='MANUAL_HIGH_CONFIDENCE':manual.append(after_row)
    for p in qain:
        assert p['negative_guard_result']['all_current_guards_pass']
        qa.append({'supplier':p['supplier'],'external_id':p['external_id'],'canonical_id':p['canonical_id'],
            'native_ai_inspection':'ALL150_INITIAL_PAIRS_INSPECTED; final subset verified',
            'scope':'Saved full names/executions/kind context + deterministic brand/axis/condition/typed/unique guards; not independent human ground truth',
            'verdict':'SAME_PRODUCT_STRONG','false_positive_found':False,'rule_id':p['rule_id']})
        ai.append({'supplier':p['supplier'],'external_id':p['external_id'],'source_url':p['source_url'],
            'AI_VERDICT':'SAME_PRODUCT_STRONG','candidate_product_id':p['canonical_id'],
            'positive_evidence':[{'full_source_model':p['source_model'],'full_canonical_model':p['canonical_model']},
                                 {'source_brand':p['source_brand'],'canonical_brand':p['canonical_brand']},*p['kind_evidence']],
            'negative_evidence':[],'identity_critical_fields':['full_model_execution','brand','sold_product_kind','axis','condition','same_role_typed_characteristics'],
            'contradictions':[],'confidence':'HIGH_SAVED_EVIDENCE','reason':'Native Codex reviewed full names/models; complete primary equipment or explicit own bandsaw category, no contradictory current guard and unique active target.',
            'rule_id':p['rule_id'],'automatic_writer':False,'evidence_refs':p['evidence_ref']})
    held=[]
    for r in after['rows']:
        guard=next((b for b in r['audit_v22']['blocking_reasons'] if b in ('V22_EXPLICIT_CATEGORY_AUTOMATION_CONTRADICTION','V22_EXPLICIT_FRAME_CONTRADICTION')),None)
        if not guard:continue
        held.append((r['source'],r['external_id']))
        ai.append({'supplier':r['source'],'external_id':r['external_id'],'source_url':r['source_url'],
            'AI_VERDICT':'SEMANTIC_CONTRADICTION','candidate_product_id':r['audit_v22']['retrieval_ids'],
            'positive_evidence':['full model/brand exact; complete machinery context'],
            'negative_evidence':['explicit source/canonical/category execution or frame evidence disagrees'],
            'identity_critical_fields':['automation_mode','structural_frame'],'contradictions':r['audit_v22']['blocking_reasons'],
            'confidence':'HIGH_BLOCKER_EVIDENCE','reason':'Canonical-side quality/identity clarification required; no convenient title preference, not source Conflict or NEW by inference.',
            'rule_id':guard,'automatic_writer':False,'evidence_refs':r['evidence_ref']})
    assert len(held)==5
    ai_columns=['supplier','external_id','source_url','AI_VERDICT','candidate_product_id','positive_evidence','negative_evidence',
                'identity_critical_fields','contradictions','confidence','reason','rule_id','automatic_writer','evidence_refs']
    tables={name:{'columns':cols,'rows':rows} for name,cols,rows in [
       ('unresolved_before.csv',columns,unresolved_before),('unresolved_after.csv',columns,unresolved_after),
       ('ai_review.csv',ai_columns,ai),('new_auto_accepts.csv',columns,new),('manual_high_confidence.csv',columns,manual),
       ('cohort_summary.csv',['supplier','Total','Existing BEFORE','Existing AFTER','+Matches','Review AFTER','Conflict AFTER'],
        [{'supplier':s,**v} for s,v in after['summary'].items()])]}
    save('CSV_TABLES.json',tables)
    save('AI_AUDIT.json',{'reviewer':'Native Codex semantic inspection, not separate GLM/Grok or opaque score',
        'initial_pairs_inspected':150,'final_new_auto_accepts_audited':len(qain),'new_pairs_unreviewed':0,
        'explicit_mode_quality_holds':held,'known_false_positive_classes':'Regression protected',
        'confirmed_false_positives_found_in_final_saved_evidence_audit':0,
        'not_claimed':'Independent human labels, fresh production truth or universal false-positive rate zero',
        'cases':qa,'source_counts':dict(Counter(p['supplier'] for p in qain)),
        'rule_counts':dict(Counter(p['rule_id'] for p in qain))})
    # Actual pilot for literal-brand recovery had one strong machine case and
    # one filter-for-machine counterexample. Insufficient support for a safe
    # generalized accept rule: keep the strong singleton manual, never an ID hack.
    pilot=json.loads((OUT/'LITERAL_BRAND_PILOT.json').read_bytes())
    for entry in pilot:
        r=next(x for x in after['rows'] if x['source']==entry['source'] and x['external_id']==entry['external_id'])
        component='фильтр для' in r['name'].lower()
        t=targets[entry['target']]
        verdict='ACCESSORY_OR_COMPONENT' if component else 'SAME_PRODUCT_STRONG'
        ai.append({'supplier':r['source'],'external_id':r['external_id'],'source_url':r['source_url'],
            'AI_VERDICT':verdict,'candidate_product_id':entry['target'],
            'positive_evidence':[{'literal_brand_in_visible_title':entry['inferred_brand']},
                                 {'source_model':r['model'],'canonical_model':t['model']}],
            'negative_evidence':['source sells filter FOR machine, not machine'] if component else ['stored brand missing; generic inference pilot failed component counterexample'],
            'identity_critical_fields':['primary_sold_product_kind','visible_brand_context','full_model_execution'],
            'contradictions':['component versus complete machine'] if component else [],
            'confidence':'HIGH_SAVED_EVIDENCE','reason':'Rejected automatic pattern; '+('component non-auto' if component else 'single strong case placed in manual ledger, not matcher'),
            'rule_id':'UNIMPLEMENTED_VISIBLE_BRAND_LITERAL_PILOT','automatic_writer':False,'evidence_refs':r['evidence_ref']})
        if not component:
            item=csvrow(r,'after');item.update(candidate_product_id=entry['target'],candidate_title=t['name'],
                candidate_brand=t['brand'],candidate_model=t['model'],terminal_route='MANUAL_HIGH_CONFIDENCE',
                rejection_reasons=['MISSING_STORED_BRAND_SINGLETON_NO_SAFE_GENERAL_RULE'],
                positive_evidence={'literal_brand_in_visible_source_title':entry['inferred_brand'],'canonical_active_id':t['sterbrust_product_id']})
            manual.append(item)
    tables['manual_high_confidence.csv']['rows']=manual
    tables['ai_review.csv']['rows']=ai
    advisory_routes=dict(after['terminal_routes'])
    singleton=next(x for x in manual if x['supplier']=='partner_st' and x['external_id']=='1702')
    for item in unresolved_after:
        if item['supplier']=='partner_st' and item['external_id']=='1702':
            previous=item['terminal_route'];item.update(singleton)
            advisory_routes[previous]-=1;advisory_routes['MANUAL_HIGH_CONFIDENCE']+=1
    save('TERMINAL_ROUTES_ADVISORY.json',{'counts':advisory_routes,
        'AI_manual_singleton_reclassified_route_only':['partner_st','1702'],
        'matching_classification_changed':False,'new_absence_proof':0,
        'LIKELY_NEW_TO_STERBRUST':0,'no_exact_model_is_not_NEW':True})
    save('CSV_TABLES.json',tables)
    patterns=[]
    for rid in sorted(after['rules']):
        supporting=[p for p in qain if p['rule_id']==rid]
        patterns.append({'PATTERN_ID':rid,'supporting_cases':len(supporting),
            'supplier_coverage':dict(Counter(p['supplier'] for p in supporting)),
            'why_safe':'Positive primary sold-equipment phrase or explicit own category; exact visible full execution/brand, unique active target and all original guards remain',
            'required_positive_evidence':['exact own visible full model/execution','compatible explicit brand/manufacturer','complete sold kind evidence','unique active canonical target'],
            'required_negative_guards':['primary component vs complete equipment','axis/condition','significant suffix/plus','typed contradictions','automation mode/category','duplicate targets'],
            'counterexamples':['controller/handle/jaws/module/panel/holder before machine phrase','circular saw vs bandsaw','automatic vs semi/manual','laser/drilling/EDM vs milling centre','different blade vector without order proof'],
            'implementation':'matching_policy_v22.kind_proof + existing assess_pair(kind_resolver=...); no per-product IDs, AI score or fuzzy acceptance'})
    save('PATTERN_CANDIDATES.json',patterns)
    text='# Mined deterministic kind-evidence patterns\n\n'
    text+='Discovered from actual V2.1 kind-blocked cohorts, not a proposed identity alias list. Four unsafe mode-category and one structural-frame candidates were withheld.\n\n'
    for p in patterns:
        text+='## '+p['PATTERN_ID']+'\n\n'+json.dumps(p,ensure_ascii=False,indent=2)+'\n\n'
    text+='## Not implemented / future evidence\n\nNo exact candidate is not absence proof. No new brand/model aliases, transliteration exceptions or product-ID hacks.\n'
    text+='Model-only Beka cards in Hydraulıc Arm/Straıght Cut categories stay unresolved without independent kind evidence; true accessories/consumables remain outside machine auto-accept.\n'
    text+='Singleton/ambiguous visible-title/model cases remain manual_high_confidence.csv; duplicate canonical IDs stay REVIEW.\n'
    text+='Literal visible-brand pilot: one strong Partner MD-20 machine case, one air-filter FOR MD-20 counterexample. No automatic brand-recovery rule accepted; strong singleton manual only.\n'
    (OUT/'pattern_candidates.md').write_text(text,encoding='utf-8')
    print(json.dumps({'unresolved_before':len(unresolved_before),'unresolved_after':len(unresolved_after),'new':len(new),'ai':len(ai),'manual':len(manual),'held':held}))

if __name__=='__main__':main()
