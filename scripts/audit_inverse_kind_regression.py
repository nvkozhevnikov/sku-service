"""Fresh29 preservation witnesses and implementation-free fix/risk assessment."""
from scripts.audit_inverse_kind import *
from scripts.audit_inverse_kind_decisions import FALSE,VALID

def main():
    inventory={(a['supplier'],a['index']):a for a in r.load(OUT/'ALL_PERSISTED_KIND_INVENTORY.json')['rows']}
    decisions={(a['supplier'],a['index']):a for a in r.load(OUT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows']}
    manifest=r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows'];protected=[]
    reasons={
      'vacuum_pump':'Own vacuum-generation purpose, sold pump, not its host CNC machine; standalone auxiliary equipment family must remain equipment.',
      'chiller':'Own coolant-circuit cooling/protection purpose; sold chiller, not complete laser machine.',
      'cutting_attachment':'Own small guillotine attachment installed on edge-bander; not complete veneer guillotine machine.',
      'saw_blade':'Own104mm cutting blade for end-trimming unit, dimensions/teeth; host edge-bander not sold object. Keep MSK40/318 distinct IDs.',
      'fixture':'Own clamping beam/stop/ruler/magnetic plate fixes/positions work on a host machine; not the complete machine.',
      'power_feeder':'Own feeding mechanism installed/used with a processing machine; preserve accessory power_feeder.',
      'reading_lens':'Own small lens for stop readout; not host panel saw.',
      'chuck_nut':'Own ER chuck nut, not chuck machine; source applicability stays contextual.',
      'controller_interface':'Own PCI NC interface card installed in computer; not complete CNC machine.',
      'glue_supply_station':'Own PUR glue melting/supply unit for application equipment; preserve auxiliary station.',
      'hand_edge_trimmer':'Own manual edge trimming/snipping purpose; not stationary machining unit. Preserve description corroboration.',
      'brush_strip':'Own brush fringe/strip100mm for CNC equipment; not complete CNC machine.',
      'inductive_sensor':'Own18mm inductive proximity sensor with low-voltage/current specs; not complete machine.',
      'knife':'Own cutter knife for MFX9512-4, host machine is application only.'}
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('OFFLINE_SQL_FORBIDDEN')):
      for m in manifest:
        a=inventory[m['supplier'],m['index']];f=r.load(OUT/a['facts']);db=r.load(o.ROOT/a['db_evidence']['record'])
        assert a['current_kind']==m['new_kind']==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
        assert a['external_id']==m['external_id'] and a['evidence_sha256']==m['evidence_sha256'] and a['response_sha256']==m['response_sha256']
        assert a['current_family']!='machine' and a['current_kind']['confidence']=='HIGH' and decisions[a['supplier'],a['index']]['semantic_decision']==VALID
        j=r.load(o.ROOT/a['journal']);outcome,product,capture=o.extract(j);assert outcome=='PERSISTED' and product.enrichment_evidence['product_kind']==m['new_kind']
        protected.append(dict(supplier=a['supplier'],index=a['index'],external_id=a['external_id'],source_product_id=m['source_product_id'],offer_id=m['offer_id'],url=a['url'],current_kind=a['current_kind'],source_purpose=f['source_description'],source_title=a['title'],price=a['price'],availability=a['availability'],evidence_sha256=a['evidence_sha256'],response_sha256=a['response_sha256'],original_observed_at=a['original_observed_at'],status='FRESH_PROTECTED_VALID_NON_MACHINE',reason=reasons[a['current_product_kind']],future_fix_regression_risk='Must preserve this own sold noun and host/application relation; positive machine priority is not a universal host-word veto.'))
      assert len(protected)==29
      a=inventory['ltt_msk',40];b=inventory['ltt_msk',318];assert a['external_id']=='1860' and b['external_id']=='1642' and a['db_evidence']['source_product_id']!=b['db_evidence']['source_product_id']
      beam=inventory['ltt_msk',129];assert beam['current_kind']['product_kind']=='fixture' and str(beam['price']['current_price'])=='42000' and beam['price']['currency']=='RUB' and beam['availability']=='OutOfStock'
      r.save(OUT/'PREVIOUS_29_REGRESSION_PROTECTION.json',dict(status='ASSESSED_AND_FRESHLY_PROTECTED',count=29,rows=protected,msk40_318_distinct=True,msk129_fixture_42000_rub_out_of_stock=True,future_code_fix_verified=False,no_fix_implemented=True,network=network))
    confirmed=r.load(OUT/'INVERSE_KIND_CONFIRMED_DEFECTS.json')['rows'];clusters=[]
    concepts={
      'MORPHOLOGY_COLLISION_IN_SHARED_LOCAL_SPARE_OVERRIDE':dict(rule='ltt_uss_html.py source_evidence local spare regex',candidate='Retain productive noun inflections for real spare heads; prevent an unconditional override when an owned complete-machine head precedes carriage/roller details. No global removal of ролик markers, and no universal prior-machine veto on parts mentioning a later host.',risk='Real rollers on/for a machine must remain parts. Feeders mentioning roller count are separate existing non-machine subtype issues; unexpected changes need separate review. Protect all29 sold objects.'),
      'SHARED_ORDERED_ALIAS_OVERRIDES_COMPLETE_MACHINE_WITHOUT_LITERAL_MACHINE_HEAD':dict(rule='product_identity._title_category_kind/_first_taxon, ordered tool aliases before absent recognized complete-machine head',candidate='Recognize precise owned machining/drilling-center and mortising-machine constructions with source corroboration, before generic фрез/сверл tool stem. Do not treat any центр/фрез/сверл token as machine; exclude tooling heads and host relations.',risk='Shared classifier has downstream consumers beyond ingestion. True cutters, drill tools, center components, manual router and29 protected rows must not become machines.'),
      'COMPLETE_MACHINE_ALIAS_COVERAGE_GAP_UNKNOWN':dict(rule='No recognized title/category alias: unknown/UNKNOWN; own source nevertheless sells a complete processing machine',candidate='Add source-supported complete saw/processing-center/press/laser-cutting/strapping concepts through owned sold-object/context rules. Exact subtype and sparse-source safeguards require operator-reviewed implementation; conceptual generic machine is a floor, not an implemented patch.',risk='A saw can be a blade, a guillotine can be a0.5kg attachment, and a press/center can be a component. Never add blanket пила/пресс/центр aliases or use price/model/weight alone. Preserve29 and full1344 non-machine corpus.')}
    for key,concept in concepts.items():
      rows=[a for a in confirmed if a['primary_cluster']==key]
      clusters.append(dict(cluster=key,count=len(rows),by_supplier=dict(Counter(a['supplier'] for a in rows)),rows=[dict(supplier=a['supplier'],index=a['index'],external_id=a['external_id'],current_kind=a['current_kind'],expected_conceptual_kind=a['expected_conceptual_kind'],db_evidence=a['db_evidence']) for a in rows],shared_local_rule=concept['rule'],safe_generic_fix_candidate=concept['candidate'],regression_risk=concept['risk'],implementation='NOT_IMPLEMENTED'))
    assert sum(c['count'] for c in clusters)==97
    terminal=r.load(OUT/'TERMINAL_INVERSE_KIND_CONFIRMED_DEFECTS.json')
    r.save(OUT/'INVERSE_KIND_DEFECT_CLUSTERS.json',dict(count=3,clusters=clusters,terminal_separate=dict(count=terminal['count'],clusters=dict(Counter(a['primary_cluster'] for a in terminal['rows']))),scope_is_analysis_not_authorization_to_fix=True))
    # Audit findings outside inverse machine-vs-nonmachine scope are explicit, not hidden acceptance.
    other=[]
    for i in [44,45,46,47,331,332,333,340,341,342,343]:
      a=inventory['ltt_msk',i];other.append(dict(supplier=a['supplier'],index=i,title=a['title'],current_kind=a['current_kind'],inverse_decision=decisions['ltt_msk',i]['semantic_decision'],note='Own object is knife/pointer/guide/clamp, not complete machine. Current table/disk subtype shows another semantic problem; VALID_NON_MACHINE certifies only inverse exclusion, not current subtype. No new repair scope authorized.'))
    r.save(OUT/'OTHER_NON_INVERSE_KIND_FINDINGS.json',dict(scope='Non-exhaustive related marker witnesses, not a second full subtype audit',rows=other,terminal151=dict(note='Family/category of vertical machining centers computed tool via фрез stem. Descriptor collision exists, but no single complete product entity established; NOT_APPLICABLE_TERMINAL, do not create DB entity.'),not_added_to_inverse_fix_scope=True))
    marker=r.load(OUT/'MARKER_COLLISION_AUDIT.json')
    marker.update(status='COMPLETE_RULES_AND_SEMANTIC_COLLISIONS_ASSESSED',confirmed_persisted_typed_collisions=31,confirmed_unknown_machine_coverage_gaps=66,confirmed_terminal_inverse=4,related_non_inverse_subtype_witnesses=other,
       confirmed_mechanisms=['Word-start productive ролик-stem collides with adjective/roller assembly details after whole-machine head.','Generic сверл/фрез stem matches machine adjective/compound when no recognized explicit станок head; ordered tool alias wins.','Complete CS275/315 saw names collide with disk productive stem; lacks complete-machine head recognition.','Unknown cases have no offending matching token: incomplete supported machine alias/context coverage, separate from substring collision.'],future_fix_scope_not_implemented=True)
    r.save(OUT/'MARKER_COLLISION_AUDIT.json',marker)
    print('Fresh29 protected, clusters',[(c['cluster'],c['count']) for c in clusters],flush=True)

if __name__=='__main__':main()
