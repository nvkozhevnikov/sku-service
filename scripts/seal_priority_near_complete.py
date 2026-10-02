"""Seal independently checked proposals; no live access or global rerun."""
from pathlib import Path
from collections import Counter
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_priority_near_complete import PRE,OUT,GROK,COMMIT,load,save,sha
from universal_supplier.priority_evidence_verification import apply_verified
from universal_supplier.characteristic_evidence import property_key

def legacy_bores(props):
    import re
    return [(p['name'],p['value']) for p in props if re.search(r'отверстие.*шпиндел',p['name'].lower())]

def main():
    assert not (OUT/'CHECKPOINT.json').exists(),'Already sealed'
    cases=load(OUT/'INDEPENDENT_22_PROOF.json');assert len(cases)==22
    promotions=load(OUT/'PROMOTIONS.json');routes=[];extra=[]
    previous={(r['source'],r['external_id']):r for r in load(PRE/'STRICT_EVALUATIONS.json')}
    for case in cases:
        i=case['priority_index'];proofs=case['assessments']
        models=[p['source_model'] for p in proofs]
        if i in range(47,53):
            assert len(proofs)==1 and len(proofs[0]['candidates'])==1
            a=proofs[0];c=a['candidates'][0]
            assert c['proof']['confirmed']
            # Discharge only the reconstructed classifier error, not arbitrary
            # old contradictions. Both sides independently contain MAIN91 and
            # COUNTER66; the old regex conflated these two distinct components.
            left=legacy_bores(a['source_properties']);right=legacy_bores(c['raw_table_properties'])
            assert {str(v) for _,v in left}=={'91','66'} and {str(v) for _,v in right}=={'91','66'}
            for properties in (a['source_properties'],c['raw_table_properties']):
                main=[p['value'] for p in properties if property_key(p['name'],'lathe')=='spindle_bore']
                assert set(main)=={'91'}
            assert c['previous_contradictions'] and all(p['key']=='spindle_bore' and p['reason']=='canonical_values_disagree' for p in c['previous_contradictions'])
            old=next(x for x in previous[a['source'],a['external_id']]['evaluations'] if x['canonical_id']==c['canonical_id'])
            prior=old['evidence']['typed_characteristics']['contradictions']
            assert prior and all(p.get('key')=='spindle_bore' and p.get('reason')=='canonical_values_disagree' for p in prior)
            proof={**c['proof'],'discharged_prior_bore_error':{'source_main_counter':left,'canonical_main_counter':right,
                'rule':'main spindle bore != counterspindle bore; independently reconstructed old regex error'}}
            extra.append({'source':a['source'],'external_id':a['external_id'],'canonical_id':c['canonical_id'],
                'canonical_name':c['canonical_name'],'proof':proof,'evidence_ref':case['capture']})
            route='RESOLVED_EXISTING';reason='Exact unique active identity; two geometry anchors agree. Old bore hold causally reconstructed as main91/counter66 conflation.'
        elif i in (0,2):
            route='CANONICAL_SIDE_EVIDENCE_REQUIRED'
            reason='Saved supplier blade/cut dimensions available; obtain canonical geometry and distinguish active full-model twins. Do not exclude a canonical ID from uniqueness just because a retrieval neighbour was rejected.'
        elif i in (1,3):
            route='CANONICAL_SIDE_EVIDENCE_REQUIRED'
            reason='Active146998/147023 duplicates;147023 lacks sufficient own geometry. Obtain comparative canonical blade/cutting tables before declaring an irreducible operator decision.'
            if i==3:reason+=' Saved330S slug is secondary; visible330C does not independently resolve duplicate canonical identity. Existing source quarantine retained; no source identity mutation.'
        elif i==4:
            route='CANONICAL_SIDE_EVIDENCE_REQUIRED';reason='Supplier visibly used; canonical114334 condition unspecified and independent dimensions incomplete. Obtain canonical condition/configuration first; human-only need not established.'
        elif i in (56,57,58):
            route='SUPPLIER_SIDE_EVIDENCE_REQUIRED';reason='Saved SMEC detail lacks spindle bore; slash-valued spindle power is not a parsed scalar and cannot supply the missing anchor. Manufacturer specification/document needed; same-page GET already200 is not justified.'
        elif i==59:
            route='FACTUAL_CONFLICT';reason='Canonical116365 own structured power5000W contradicts its own description0.5kW (=500W). MK2/КМ2 spelling cannot repair tenfold numeric disagreement. No auto-correction.'
            c=proofs[0]['candidates'][0]
            assert any(p['name']=='Мощность, Вт' and str(p['value'])=='5000' for p in c['raw_properties'])
            assert any(p['name']=='Мощность' and str(p['value'])=='0.5 кВт' for p in c['raw_table_properties'])
        elif i==60:
            route='CANONICAL_SIDE_EVIDENCE_REQUIRED';reason='Net180kg and gross210kg are different measures, not a proven product contradiction. Canonical Производство=Германия / Страна производства=Китай need label-role clarification; КМ4/МК-4 spelling is not independent identity proof. Keep hold, do not guess country semantics.'
        elif i in (61,62,63,65,66):
            route='FACTUAL_CONFLICT';reason='Canonical own title names a different full model from stored PP-600F model field. This is a target-card integrity conflict, not proof that unrelated retrieved supplier PP600F is itself contradictory.'
            for a in proofs:
                for c in a['candidates']:
                    assert c['canonical_model']=='PP-600F' and 'PP-600F' not in c['canonical_name']
        elif i==64:
            route='FACTUAL_CONFLICT';reason='Exact PP600F source motor550W versus canonical40571 motor570W. Do not round or merge values; unrelated malformed PP600F canonical neighbours also prevent unique assignment.'
        else:raise AssertionError('Unclassified case '+str(i))
        routes.append({'priority_index':i,'models':models,'priority_url':case['priority_url'],
            'source_records':[{'source':a['source'],'external_id':a['external_id']} for a in proofs],
            'canonical_ids':sorted({c['canonical_id'] for a in proofs for c in a['candidates']}),
            'route':route,'reason':reason,'independent_proof_ref':'INDEPENDENT_22_PROOF.json',
            'same_url_supplier_get_required':False})
    allpromotions=promotions+extra;save('ALL_PROMOTIONS.json',allpromotions);save('TERMINAL_22_ROUTES.json',routes)
    before=load(PRE/'MATCHING_AFTER.json');after=apply_verified(before['rows'],allpromotions)
    assert apply_verified(after,allpromotions)==after
    changed={(p['source'],p['external_id']) for p in allpromotions}
    for old,new in zip(before['rows'],after):
        if (old['source'],str(old['external_id'])) not in changed:assert old==new
        for key in ('source','external_id','model','execution','name','source_url','observed_properties','properties'):assert old.get(key)==new.get(key)
    save('MATCHING_ACCEPTED.json',{**before,'rows':after,'predecessor':str(PRE/'CHECKPOINT.json'),
        'summary':{'overall':{'Existing':505,'READY_TO_CREATE_IDENTITY':0,'Review':3643,'Conflict':259}}})
    counts=Counter(r['classification'] for r in after);assert counts=={'EXISTING_CONFIRMED':505,'REVIEW':3643,'CONFLICT':259}
    routescount=Counter(r['route'] for r in routes);assert sum(routescount.values())==22
    per={s:dict(Counter(r['classification'] for r in after if r['source']==s)) for s in sorted({r['source'] for r in after})}
    summary={'overall':{'Existing':505,'READY_TO_CREATE_IDENTITY':0,'Review':3643,'Conflict':259},'source_rows':4407,
        'per_supplier':per,'potential45_accepted':45,'potential45_rejected':0,'additional22_resolved':6,
        'terminal22_routes':dict(routescount),'true_human_required_proven':routescount.get('TRUE_HUMAN_REQUIRED',0),
        'canonical_evidence_needed':routescount['CANONICAL_SIDE_EVIDENCE_REQUIRED'],
        'supplier_evidence_needed':routescount['SUPPLIER_SIDE_EVIDENCE_REQUIRED'],
        'supplier_repeat_get_required':0,'supplier_new_document_evidence_required':3,
        'factual_target_integrity_conflicts':routescount['FACTUAL_CONFLICT'],
        'previous454_existing_preserved':True,'previous259_conflicts_preserved':True,
        'sql':0,'http':0,'global_matching_rerun':False,'source_ingestion':False,
        'replay_exact_noop':True,'final_xml_xlsx_published':False,
        'scope_note':'22 URL-level priority cases; shared source references are not independent source counts. Target integrity routes do not change unrelated source classifications.'}
    save('VERIFIED_SUMMARY.json',summary)
    save('EVIDENCE_NEXT_PLAN.json',[r for r in routes if r['route'] in ('CANONICAL_SIDE_EVIDENCE_REQUIRED','SUPPLIER_SIDE_EVIDENCE_REQUIRED')])
    save('CHECKPOINT.json',{'status':'PRIORITY_NEAR_COMPLETE_OFFLINE_INTEGRATED',
        'resume_from':'reports/RC_LOCAL/PRIORITY_NEAR_COMPLETE_INTEGRATION_2026-10-02/CHECKPOINT.json',
        'predecessor':str(PRE/'CHECKPOINT.json'),'grok_commit':COMMIT,
        'authoritative_matching':'MATCHING_ACCEPTED.json','verification45':'VERIFY_45.json',
        'terminal22':'TERMINAL_22_ROUTES.json','summary':'VERIFIED_SUMMARY.json','overall':summary['overall'],
        'completed_do_not_repeat':['67priority evidence integration','45strict canonical proofs','6main/counterspindle hold reconstruction','22exclusive terminal routing'],
        'http':0,'sql':0,'final_xml_xlsx_published':False,'next_action':'Acquire only missing labelled canonical comparison/condition/country evidence or SMEC manufacturer documents; do not repeat existing supplier page GETs.',
        'hashes':'ARTIFACT_SHA256.json'})
    print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
