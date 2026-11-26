"""Explicit reviewed decisions for this frozen impact corpus, not classifier rules."""
from scripts.audit_inverse_kind import *
from scripts.audit_inverse_kind_markers import rules
from bs4 import BeautifulSoup

SPARE={'ltt_msk':{69,72,76,77,78,79,80,81,82,84,86},'ltt_sib':{1057}}
TOOL={'ltt_msk':{117},'ltt_sib':{1099},'ltt_yug':set(range(288,294)),'uss_stanko':{89,*range(103,113)}}
UNKNOWN={'ltt_msk':{89,90,91,92,248,249,250,290},'ltt_sib':{*range(961,967),1071,1072,1073},'ltt_yug':{132,133,206,207,208,209,*range(306,316),*range(322,341),346,347,467,468,545,546},'uss_stanko':{41,42,43,44,46,47,48,49}}
OPERATORS={'ltt_msk':{48,49,126,127,*range(149,164),170,171,172},'ltt_sib':{870,871,979,980,*range(991,1013),1113,1114,1117,1118},'ltt_yug':{272,273,318,319,320,321,341,342,343,344,*range(348,369)},'uss_stanko':{21,23,24,26,87,88,288,368,369,372,374,377,382,388,390,393,398,399,400,401,404,405,406}}
FALSE='CONFIRMED_FALSE_NON_MACHINE_COMPLETE_MACHINE'
VALID='VALID_NON_MACHINE'
REVIEW='OPERATOR_REVIEW_REQUIRED'
NA='NOT_APPLICABLE_TERMINAL'

def listed(groups,a):return a['index'] in groups.get(a['supplier'],set())

def decision(a,terminal=False):
    if terminal:
        if a['index'] in {50,51,182,221}:
            return FALSE,('SHARED_ORDERED_ALIAS_OVERRIDES_COMPLETE_MACHINE_WITHOUT_LITERAL_MACHINE_HEAD' if a['index']==221 else 'COMPLETE_MACHINE_ALIAS_COVERAGE_GAP_UNKNOWN'), 'Own main content and machine-level specifications describe complete saw/press executions; independent variants remain terminal, not a blade or press component.'
        if a['index'] in {22,27,285,367,371,373,375,376,378,380,386,387,391}:
            return REVIEW,'AUXILIARY_OR_LIFTING_EQUIPMENT_TAXONOMY_POLICY', 'Own pages describe compressor/extraction/lifting units; unknown is not an asserted accessory kind. Complete-equipment vs machine-family taxonomy requires operator decision; no forced machine label.'
        return NA,'TERMINAL_CATEGORY_ACCESSORY_REFERENCE_OR_FAMILY_PAGE', 'Terminal source descriptor is category/family/reference, attachment/tooling or unresolved page; not evidence for creating one complete-machine entity. Preserve AMBIGUOUS/MULTI_VARIANT. Any descriptor alias collision recorded separately.'
    if listed(SPARE,a):return FALSE,'MORPHOLOGY_COLLISION_IN_SHARED_LOCAL_SPARE_OVERRIDE','Own title/model, machining purpose and machine-level construction/specifications identify the complete machine; local ролик-stem match describes its carriage/rollers, not the sold spare.'
    if listed(TOOL,a):return FALSE,'SHARED_ORDERED_ALIAS_OVERRIDES_COMPLETE_MACHINE_WITHOUT_LITERAL_MACHINE_HEAD','Own sold object is a CNC machining/drilling center or mortising machine, corroborated by own category/order identity and purpose/specifications where published; productive фрез/сверл tool alias wins because title lacks literal станок.'
    if listed(UNKNOWN,a):return FALSE,'COMPLETE_MACHINE_ALIAS_COVERAGE_GAP_UNKNOWN','Own title/model/category and own machining/pressing/cutting/strapping purpose or machine-level source context identify a complete processing machine. Current unknown/UNKNOWN is a missing machine classification, not an asserted spare label; generic conceptual machine is supported, exact subtype remains a future reviewed choice.'
    if listed(OPERATORS,a):
        if a['supplier']=='ltt_msk' and a['index'] in {170,171,172}:return REVIEW,'PERSISTED_EDITORIAL_OR_PAGE_ROLE_REVIEW','Own source text is editorial, not sufficient proof of one current complete-machine sold object. Page-role/ingestion issue is separate; no inverse-kind correction inferred.'
        if a['supplier']=='ltt_yug' and a['index'] in {272,273}:return REVIEW,'PORTABLE_EDGE_BANDING_MACHINE_VS_HAND_TOOL','Own wording shows portable/manual edge-banding device. Do not force industrial-machine or hand-tool type without operator policy; distinct from previously fixed manual trimmers.'
        return REVIEW,'AUXILIARY_OR_LIFTING_EQUIPMENT_TAXONOMY_POLICY','Own source sells standalone auxiliary, coating, marking, lifting or measuring equipment. Current unknown does not positively assert a spare/accessory; exact equipment vs machine taxonomy must be decided explicitly. Preserve as operator review, not a confirmed wrong-machine-family assertion.'
    return VALID,'OWN_SOLD_OBJECT_IS_TOOL_COMPONENT_FIXTURE_OR_AUXILIARY','Own sold title names the actual tool/blade/knife/part/fixture/feed/cooling/vacuum/bench/handling accessory or consumable. Machine/category words describe its application, workpiece or host; no complete processing machine is sold by this row. This decision is inverse-safety only, not acceptance of every current subtype.'

def main():
    rows=r.load(OUT/'INVERSE_KIND_RISK_CANDIDATES.json')['rows'];terms=r.load(OUT/'TERMINAL_INVERSE_KIND_RISK_CANDIDATES.json')['rows']
    outputs=[];terminal_outputs=[]
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('OFFLINE_SQL_FORBIDDEN')):
      for terminal,inputs,target in [(False,rows,outputs),(True,terms,terminal_outputs)]:
       for a in inputs:
        assert r.digest((OUT/a['facts']).read_bytes())==a['facts_sha256'];f=r.load(OUT/a['facts'])
        status,cluster,reason=decision(a,terminal)
        item=dict(a);item.update(semantic_decision=status,primary_cluster=cluster,reason=reason,actually_sold=a['title'],own_purpose_excerpt=f['source_description'][:1500],own_headings=f['headings'],expected_conceptual_kind='machine' if status==FALSE else 'OPERATOR_TAXONOMY_OR_SOURCE_ROLE_DECISION' if status==REVIEW else 'non_machine_or_no_single_terminal_entity',
           source_class='PACKAGED-EVIDENCE',technical_class='REPOSITORY-VERIFIED',semantic_class='INFERENCE_FROM_OWN_SOURCE',prior_semantic_pass_used=False,db_correction_candidate=not terminal and status==FALSE,terminal_outcome_preserved=terminal,full_qa_acceptance=False)
        # Exact real call inputs/result for each confirmed machine and every29 protected row.
        if status==FALSE:
          e=a;j=r.load(o.ROOT/a['journal']);m=j['result'];body=(r.OUT/'EVIDENCE'/a['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
          calls=[];original=identity.classify_product_kind
          def spy(name,category='',*,own_description=''):
            answer=original(name,category,own_description=own_description);calls.append(dict(title=name,category=category,own_description=own_description,result=asdict(answer)));return answer
          with patch.object(identity,'classify_product_kind',new=spy):product=r.parse(a['supplier'],body,m['final_url'])
          assert len(calls)==1 and product.enrichment_evidence['product_kind']==a['current_kind']
          item['real_classifier_inputs_and_result']=calls[0]
          local_head=a['title'].lower().split(' для ')[0];msp=re.search(r'\b(?:ролик\w*|запчаст\w*|ремень\w*)',local_head)
          if cluster=='MORPHOLOGY_COLLISION_IN_SHARED_LOCAL_SPARE_OVERRIDE':assert msp and calls[0]['result']['product_kind'] in MACHINE;item['offending_match']=dict(regex=r'\b(?:ролик\w*|запчаст\w*|ремень\w*)',match=msp.group(),span=msp.span(),scope=local_head)
          else:item['offending_match']=dict(matched_alias=a['current_kind']['matched_alias'],source=a['current_kind']['source'],kind=a['current_product_kind'])
          r.save(OUT/('TERMINAL_CONFIRMED_SOURCE_TRACES' if terminal else 'CONFIRMED_SOURCE_TRACES')/a['supplier']/f"{a['index']:06d}.json",item)
        target.append(item)
      assert len(outputs)==len(rows)==800 and len(terminal_outputs)==len(terms)==61
      allowed={FALSE,VALID,REVIEW,NA};assert all(a['semantic_decision'] in allowed for a in outputs+terminal_outputs)
      declared={(s,i) for group in [SPARE,TOOL,UNKNOWN] for s,ids in group.items() for i in ids}
      actual={(a['supplier'],a['index']) for a in outputs if a['semantic_decision']==FALSE};assert declared==actual
      review_declared={(s,i) for s,ids in OPERATORS.items() for i in ids};assert review_declared <= {(a['supplier'],a['index']) for a in outputs}
      counts=Counter(a['semantic_decision'] for a in outputs);tc=Counter(a['semantic_decision'] for a in terminal_outputs)
      r.save(OUT/'INVERSE_KIND_SEMANTIC_DECISIONS.json',dict(status='COMPLETE',count=800,semantically_reviewed=800,unreviewed=0,decision_counts=dict(counts),rows=outputs,review_scope='Inverse complete processing machine vs non-machine/unknown. Auxiliary/lifting taxonomy gaps and page-role uncertainty remain operator review; non-machine subtype correctness is not certified.'))
      confirmed=[a for a in outputs if a['semantic_decision']==FALSE]
      r.save(OUT/'INVERSE_KIND_CONFIRMED_DEFECTS.json',dict(count=len(confirmed),affected_suppliers=sorted({a['supplier'] for a in confirmed}),by_supplier=dict(Counter(a['supplier'] for a in confirmed)),by_current_kind=dict(Counter(a['current_product_kind'] for a in confirmed)),typed_false_non_machine=sum(a['current_product_kind']!='unknown' for a in confirmed),unknown_machine_coverage_gaps=sum(a['current_product_kind']=='unknown' for a in confirmed),rows=confirmed,no_fix_implemented=True))
      r.save(OUT/'TERMINAL_INVERSE_KIND_SEMANTIC_DECISIONS.json',dict(count=61,semantically_reviewed=61,decision_counts=dict(tc),rows=terminal_outputs,db_entities_created=0,terminal_outcome_delta=0))
      r.save(OUT/'TERMINAL_INVERSE_KIND_CONFIRMED_DEFECTS.json',dict(count=tc[FALSE],rows=[a for a in terminal_outputs if a['semantic_decision']==FALSE],existing_db_correction_required=False,terminal_descriptor_only=True))
      r.save(OUT/'DECISION_OFFLINE_PROOF.json',dict(**network,sql_connections=0,db_writes=0,source_writes=0,full_qa_continued=False))
    print('Decisions:',dict(counts),'terminal',dict(tc),'confirmed suppliers',Counter(a['supplier'] for a in confirmed),flush=True)

if __name__=='__main__':main()
