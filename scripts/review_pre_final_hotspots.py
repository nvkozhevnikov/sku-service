"""Explicit evidence review ledger, not product classification or a repair."""
from scripts.audit_pre_final_hotspots import *
from pathlib import Path

WRONG_SPARE={('ltt_msk',i) for i in [174,175,176,177,178,192,193,194]}|{('ltt_sib',i) for i in [1018,1019,1021,1022]}
UNKNOWN_FEEDER={('ltt_msk',173)}|{('ltt_sib',i) for i in [1016,1017,1020]}|{('ltt_yug',i) for i in range(372,382)}
TRUE_FEEDER={('ltt_msk',203),('ltt_msk',240)}
TRUE_SPARE={('ltt_msk',298)}
FALSE_TABLE={('ltt_msk',i):('knife' if i in [44,45,46,47] else 'spare' if i in [340,341,342] else 'fixture') for i in [44,45,46,47,340,341,342,343]}
TRUE_TABLE={('ltt_msk',346)}|{('ltt_yug',i) for i in [129,130,131]}|{('uss_stanko',i) for i in [54,56,63,146,402]}
FALSE_CONSUMABLE={('ltt_msk',294),('ltt_msk',295)}
TRUE_CONSUMABLE={('ltt_msk',296),('ltt_msk',297)}

def key(a):return a['entry']['supplier'],a['entry']['index']

def verdict(group,a):
    k=key(a);kind=a['current_kind']['product_kind']
    if group=='FEEDER_FAMILY':
      if k in WRONG_SPARE:
        assert kind=='spare';return 'CONFIRMED_POWER_FEEDER_WRONG_SPARE','power_feeder','ROLLER_DETAIL_OVERRIDES_POWER_FEEDER','Own automatic-feeder noun, execution and powered-feed purpose identify the complete feeding device; subordinate roller count/adjective describes its construction, not a replacement roller.'
      if k in UNKNOWN_FEEDER:
        assert kind=='unknown';return 'CONFIRMED_POWER_FEEDER_UNKNOWN','power_feeder','POWER_FEEDER_REFINEMENT_GATED_BY_MACHINE_PRIOR','Own complete automatic-feeder noun and named execution identify feeding equipment, not a part for a feeder. Own feed purpose/speed/power corroborates where supplied; sparse YUG372/380/381 lack some specifications, which are not invented. Existing power_feeder is supported; classifier currently returns unknown before functional refinement.'
      if k in TRUE_FEEDER:
        assert kind=='power_feeder';return 'VALID_POWER_FEEDER',kind,None,'Own complete powered feeder and feeding purpose support the current subtype; accepted previous29 witness must remain exactly unchanged.'
      assert k in TRUE_SPARE and kind=='spare';return 'VALID_SPARE_FOR_FEEDER',kind,None,'Own sold noun is rollers FOR feeders, with replacement dimensions120x60x35 and mounting holes. The feeder models describe compatibility. Keep spare; never infer a complete feeder from host mention.'
    if group=='TABLE_STEM':
      if k in FALSE_TABLE:
        assert kind=='table' and a['current_kind']['source']=='category';target=FALSE_TABLE[k]
        reason={'knife':'Own knife noun, cutting geometry and installation purpose identify knives; host woodworking adjective does not prove a sold table.','spare':'Own upper/lower saw-blade guide with ball bearings for a named bandsaw is a replacement guiding assembly, supported by existing ingestion spare; no invented saw-guide subtype.','fixture':'Own eccentric clamp purpose is holding a workpiece; existing fixture is supported, rather than a table inferred from woodworking category.'}[target]
        return 'FALSE_TABLE_STEM',target,'WOODWORKING_ADJECTIVE_MATCHES_TABLE_NOUN',reason
      assert k in TRUE_TABLE and kind=='table';return 'VALID_TABLE',kind,None,'Own table/workbench/lifting-table/rotary-table noun and own function identify the table. USS54 woodworking adjective is corroborated by actual workbench noun and construction; USS54/146 remain terminal MULTI_VARIANT/AMBIGUOUS with no fabricated entity.'
    if k in FALSE_CONSUMABLE:
      assert kind=='consumable';return 'CONFIRMED_CHILLER_WRONG_CONSUMABLE','chiller','MIXED_CATEGORY_CONSUMABLE_OVERRIDES_OWN_CHILLER','Own chiller execution and autonomous/forced liquid-cooling purpose identify the complete cooling unit. Mixed consumables category cannot replace its sold-object identity; CW3000 radiator principle is preserved without inventing compressor refrigeration.'
    assert k in TRUE_CONSUMABLE and kind=='consumable';return 'VALID_CONSUMABLE',kind,None,'Own CO2 reflecting molybdenum mirror of20/25mm is the replacement optical component in the source consumables category, not a complete cooling unit; retain current consumable with no invented replacement interval.'

def trace(a):
    scans=[];first=identity._first_taxon
    def observer(text):
        result=first(text);winner=result[0].kind if result[0] else None;examined=[]
        for taxon in identity.PRODUCT_TAXONOMY:
          for regex in taxon.aliases:
            match=re.search(regex,text,re.I);examined.append(dict(kind=taxon.kind,regex=regex,match=match.group() if match else None))
            if match:break
          if examined and examined[-1]['match'] is not None:break
        scans.append(dict(input=text,caller=inspect.currentframe().f_back.f_code.co_name,winner=winner,matched_alias=result[1],examined_aliases=examined));return result
    e=a['entry'];j=r.load(o.ROOT/e['journal']);m=j['result'];body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
    with patch.object(identity,'_first_taxon',observer):parsed=asdict(r.parse(e['supplier'],body,m['final_url']))
    assert r.digest(r.encoded(parsed))==a['payload_sha256']
    pre=a['classifier_inputs']['result'];head=a['title'].lower().split(' для ')[0];regex=r'\b(?:ролик\w*|запчаст\w*|ремень\w*)';hit=re.search(regex,head)
    from universal_supplier.adapters.ltt_uss_html import source_evidence
    locations=[]
    for fn in [first,identity._title_category_kind,identity.classify_product_kind,source_evidence]:
        lines,start=inspect.getsourcelines(fn);path=Path(inspect.getsourcefile(fn));locations.append(dict(function=fn.__name__,file=str(path.relative_to(o.ROOT)),start_line=start,end_line=start+len(lines)-1,sha256=r.digest(path.read_bytes())))
    return dict(executed_taxon_scans=scans,pre_adapter_kind=pre,adapter_head=head,adapter_spare_regex=regex,adapter_spare_match=hit.group() if hit else None,table_regex=r'\bстол\w*\b',consumable_regex=r'\bрасходн\w*\b',functional_feeder_regex=r'\bавтоподатчик\w*\b',functional_chiller_regex=r'\bчиллер\w*\b',functional_gate='classify_product_kind returns prior when prior family is not machine, after complete-processing-machine attempt; power_feeder/chiller rules below are not reached',repository_rule_locations=locations,repository_rules='REPOSITORY_RULES.json')

def main():
    assert not (H/'HOTSPOT_CONFIRMED_DEFECTS.json').exists();inventories={g:r.load(H/(g+'_INVENTORY.json')) for g in ['FEEDER_FAMILY','TABLE_STEM','CONSUMABLE_CATEGORY']}
    expected={'FEEDER_FAMILY':WRONG_SPARE|UNKNOWN_FEEDER|TRUE_FEEDER|TRUE_SPARE,'TABLE_STEM':set(FALSE_TABLE)|TRUE_TABLE,'CONSUMABLE_CATEGORY':FALSE_CONSUMABLE|TRUE_CONSUMABLE}
    preview=r.load(H/'PRELIMINARY_SAVED_BASELINE_SELECTION.json')['groups'];terminal=r.load(H/'PRELIMINARY_TERMINAL_SELECTION.json')['rows'];defects={};clusters={};all_decisions={}
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('HOTSPOT_SQL_PROHIBITED')):
      for group,inventory in inventories.items():
        assert {key(a) for a in inventory['rows']}==expected[group],('SCOPE_DRIFT_REQUIRES_REVIEW',group)
        for a in inventory['rows']:
          original=next((b for b in preview[group] if key(b)==key(a)),None) or next(b for b in terminal if (b['entry']['supplier'],b['entry']['index'])==key(a))
          assert original['current_kind']==a['current_kind'] and r.digest((H/original['packet']).read_bytes())==a['source_packet_sha256']
          decision,target,cluster,reason=verdict(group,a);packet=r.load(H/a['source_packet']);text=packet['content_text'];pos=text.find('Назначение')
          a.update(supplier=a['entry']['supplier'],row=a['entry']['index'],stable_id=a['entry']['external_id'],source_product_id=a.get('db_evidence',{}).get('source_product_id'),offer_id=a.get('db_evidence',{}).get('offer_id'),current_product_kind=a['current_kind']['product_kind'],current_confidence=a['current_kind']['confidence'],matched_alias=a['current_kind']['matched_alias'],source_of_alias=a['current_kind']['source'],semantic_decision=decision,semantic_reason=reason,expected_conceptual_kind=target,expected_existing_kind=target,existing_target_registered=target in identity.TAXON_BY_KIND,existing_source_ingestion_kind=target=='spare',defect_cluster=cluster,own_description_purpose=text[pos:] if pos>=0 else text,description_scope='Own scoped source DOM; no purpose section invented when absent',own_source_title=packet.get('own_title') or packet.get('headings'),commercial_facts=dict(parsed_price=a['price'],parsed_availability=a['availability'],own_numeric_cells=packet.get('numeric_price_cells'),own_por_cells=packet.get('por_cells'),own_stock_spans=packet.get('stock_spans'),source_tables=packet.get('tables'),audit_normalization_or_fx_applied=False),exact_root_cause_trace=trace(a),business_change_applied=False)
          if cluster:
            assert a['persisted'];defects[key(a)]=a;clusters.setdefault(cluster,[]).append(key(a))
        name={'FEEDER_FAMILY':'FEEDER_SEMANTIC_DECISIONS','TABLE_STEM':'TABLE_STEM_SEMANTIC_DECISIONS','CONSUMABLE_CATEGORY':'CONSUMABLE_SEMANTIC_DECISIONS'}[group]
        r.save(H/(name+'.json'),dict(status='COMPLETE',count=len(inventory['rows']),reviewed=len(inventory['rows']),unreviewed=0,persisted_count=inventory['persisted_count'],terminal_count=inventory['terminal_count'],decisions=dict(Counter(a['semantic_decision'] for a in inventory['rows'])),rows=inventory['rows'],semantic_evidence_class='INFERENCE_FROM_PACKAGED_OWN_SOURCE',no_prior_semantic_pass_reused=True));all_decisions[group]=inventory['rows']
      assert len(defects)==36 and len(clusters)==4
      r.save(H/'HOTSPOT_CONFIRMED_DEFECTS.json',dict(count=36,by_supplier=dict(Counter(k[0] for k in defects)),target_counts=dict(Counter(a['expected_existing_kind'] for a in defects.values())),rows=list(defects.values()),source_writes=0,db_writes=0,not_authorized_for_apply=True))
      r.save(H/'HOTSPOT_DEFECT_CLUSTERS.json',dict(count=4,clusters={label:dict(count=len(keys),rows=[dict(supplier=k[0],row=k[1],stable_id=defects[k]['entry']['external_id'],source_product_id=defects[k]['db_evidence']['source_product_id'],offer_id=defects[k]['db_evidence']['offer_id']) for k in keys],repository_rule='REPOSITORY_RULES.json',trace_records='HOTSPOT_CONFIRMED_DEFECTS.json') for label,keys in clusters.items()}))
      r.save(H/'REVIEW_HTTP_SQL_ZERO_PROOF.json',dict(**network,sql_connections=0,db_writes=0,source_writes=0))
    print('REVIEW COMPLETE50; feeder29/table17/consumable4; confirmed36; clusters4; unreviewed0',flush=True)

if __name__=='__main__':main()
