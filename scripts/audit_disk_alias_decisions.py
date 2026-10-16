"""Record explicit reviewed disk-audit cohorts, not production classification."""
from scripts.audit_disk_alias import *

# Audit-only row groups: all own headings/purposes were inspected. No ID rules
# are installed in business logic. Each record retains its own evidence/hash.
GROUPS=[
 ('VALID_BLADE','ltt_msk',[42]),
 ('VALID_WHEEL','ltt_msk',[319,320,345]),
 ('VALID_BLADE','ltt_sib',list(range(30,156))+[1172,1197]),
 ('VALID_WHEEL','ltt_sib',[1175,1176,1177,1178,1179]),
 ('FALSE_HEATER','ltt_msk',[324]),
 ('FALSE_FEED_WHEEL','ltt_msk',[330]),
 ('FALSE_LASER','ltt_msk',[331,332,333]),
 ('FALSE_KNIFE','ltt_sib',[547,548,549,550]),
 ('FALSE_BUSHING','ltt_sib',[1202]),
 ('CUTTER_HOST_CONTEXT','ltt_msk',[41]),
 ('WHOLE_MACHINE_CONTEXT','ltt_msk',[68,137,225,226,227,228,229,230,231,232,233,234,235,236,237,355,366]),
 ('CLAMP_HOST_CONTEXT','ltt_msk',[307]),
 ('KNIFE_KIT_HOST_CONTEXT','ltt_msk',[347]),
 ('CUTTER_WORKPIECE_CONTEXT','ltt_sib',[190,191,192]),
 ('CUTTER_GROOVE_CONTEXT','ltt_sib',[240,241,242]),
 ('WHOLE_MACHINE_CONTEXT','ltt_sib',[886,887,888,889,892,916,917,918,919,924,926,927,928,929,930,931,932,933,935,937,939,950,953,954,978,1052,1055,1056,1057,1058,1059,1060,1066,1067,1070,1108,1250,1251,1257,1258,1259,1261,1262,1263,1265]),
 ('TRIMMER_CONTEXT_ONLY','ltt_sib',[958]),
 ('FEEDER_CONTEXT_ONLY','ltt_sib',[1017,1018,1019,1020,1021,1022]),
 ('ROLLER_HOST_CONTEXT','ltt_sib',[1198,1199,1200,1201]),
 ('WHOLE_MACHINE_CONTEXT','ltt_yug',[439,440,441,442,443,444,445,547,585]),
 ('WHOLE_MACHINE_CONTEXT','uss_stanko',[296,354]),
 ('TERMINAL_MACHINE_FAMILY','uss_stanko',[221]),
 ('TERMINAL_EDITORIAL','uss_stanko',[225]),
]

REASONS={
 'VALID_BLADE':'Own sold item is an individual circular saw blade: named пила дисковая/дисковая пила/пильный диск, with diameter,bore,kerf/tooth specification and cutting/application context. Adjective дисковая is legitimate here; host saw is not the sold object.',
 'VALID_WHEEL':'Own sold noun круг denotes a polishing or abrasive sharpening wheel with explicit dimensions/function. This is a real circular consumable, not a round adjective applied to a different component.',
 'FALSE_HEATER':'Own sold ТЭН is a220V/1000W electrical plate heating element for a glue bath. круглый describes shape, not a disk consumable. Host edgebander is applicability only.',
 'FALSE_FEED_WHEEL':'Own sold feeding wheel/roller for MC9060/MC9060A transports workpieces. круглопалочных describes the host category; it is not a cutting/abrasive disk.',
 'FALSE_LASER':'Own laser line marker comprises optical emitter,power supply and bracket, with wavelength/output specifications. круглопильных describes host saw category; no disk is sold.',
 'FALSE_KNIFE':'Own rectangular knife pair for MC9060 has length,width,thickness and tool-steel specification. круглопалочных describes host category; no circular disk is sold.',
 'FALSE_BUSHING':'Own item is a bushing for MC9060 with outer/inner diameters80/16 or80/36. Round geometry does not make a cutting/abrasive disk; круглопалочных belongs to host category.',
 'CUTTER_HOST_CONTEXT':'Own sold radius cutter rounds handle ends; круглопалочных describes its application. Final tooling is retained; there is no disk repair candidate.',
 'WHOLE_MACHINE_CONTEXT':'Own heading/purpose sells a complete processing or sharpening machine. Disk/circle stems describe machine type,processed workpiece,installed blade/wheel or serviced tool. They do not establish a separately sold disk. Existing non-disk kind is retained; unrelated kind accuracy is not reopened.',
 'CLAMP_HOST_CONTEXT':'Own eccentric clamp holds workpieces; circular-saw wording enumerates compatible hosts. Current unknown remains conservative; disk is not the sold object.',
 'KNIFE_KIT_HOST_CONTEXT':'Own kit contains two shaped knives; круглопалочного identifies host. Current accessory grouping is retained without reopening its subtype.',
 'CUTTER_WORKPIECE_CONTEXT':'Own cutter kit manufactures curved barrel boards; круговой qualifies produced boards/workpieces, not a sold disk. Existing grouping retained, no unrelated kind repair.',
 'CUTTER_GROOVE_CONTEXT':'Own groove/radius cutter machines channels; circle/disk references describe work/applicability. Existing tooling result retained; no independently sold disk.',
 'TRIMMER_CONTEXT_ONLY':'Own JB32S end-edge trimmer removes overhang, not a sold disk. Existing protected uncertainty/type is retained; this audit does not reopen manual-trimmer classification.',
 'FEEDER_CONTEXT_ONLY':'Own powered feeder advances workpieces into processing machines; round/circular words describe compatible machines or drive rollers. It is not a sold disk. Existing unknown/spare result is preserved without reopening feeder taxonomy.',
 'ROLLER_HOST_CONTEXT':'Own feed roller is a mechanical spare. Broad host-category circle alias yields a disk prior but actual adapter explicitly overrides it to spare. Final spare is protected; no persisted disk defect.',
 'TERMINAL_MACHINE_FAMILY':'Own CS275/CS315 page sells multiple complete metal-cutting saw executions with motors,vises and individual commercial rows. Final computed machine is correct against disk alias; MULTI_VARIANT and absence of entities remain unchanged.',
 'TERMINAL_EDITORIAL':'Own heading/body are recommended cutting modes,tooth pitch and operating advice, not a single sold object. Computed disk from дисковых is semantically inapplicable; AMBIGUOUS already prevents persistence. No entity or terminal eligibility change is proposed.'}

def record():
    assert not (D/'DISK_ALIAS_SEMANTIC_DECISIONS.json').exists()
    corpus=r.load(D/'DISK_ALIAS_CORPUS_HITS.json');lookup={(a['entry']['supplier'],a['entry']['index']):a for a in corpus['rows']}
    groups={}
    for group,supplier,indices in GROUPS:
      for index in indices:
        key=supplier,index;assert key not in groups;groups[key]=group
    assert set(groups)==set(lookup),(set(lookup)-set(groups),set(groups)-set(lookup))
    decisions=[];defects=[];valid=[];terminal=[]
    targets={'FALSE_HEATER':'spare','FALSE_FEED_WHEEL':'spare','FALSE_LASER':'accessory','FALSE_KNIFE':'knife','FALSE_BUSHING':'spare'}
    for key,a in lookup.items():
      group=groups[key];packet=r.load(D/a['source_packet']);assert r.digest((D/a['source_packet']).read_bytes())==a['source_packet_sha256']
      text=packet['content_text'];pos=text.find('Назначение');purpose=text[pos:pos+1700] if pos>=0 else text[:1700]
      is_defect=group.startswith('FALSE_');is_valid=group.startswith('VALID_');is_terminal=group.startswith('TERMINAL_')
      decision='VALID_DISK' if is_valid else ('NOT_APPLICABLE_TERMINAL' if is_terminal else 'FALSE_DISK_ALIAS')
      assert not is_defect or (a['persisted'] and a['currently_disk'])
      assert not is_valid or (a['persisted'] and a['currently_disk'])
      expected=targets[group] if is_defect else a['current_kind']['product_kind']
      record=dict(a,semantic_decision=decision,review_group=group,actually_sold=packet.get('own_title') or a['title'],own_purpose_excerpt=purpose,reason=REASONS[group],confirmed_persisted_classification_defect=is_defect,terminal_computed_false_disk_alias=group=='TERMINAL_EDITORIAL',current_non_disk_winner_preserved=not is_defect and not is_valid and not is_terminal,expected_existing_kind=expected if not is_terminal else None,exact_subtype_operator_review=False,semantic_class='INFERENCE_FROM_OWN_SOURCE',review_method='Each own heading/purpose and scoped disk matches inspected; explicit audit cohort rows recorded, no automatic alias-hit-to-defect promotion',prior_semantic_pass_used=False)
      if is_defect:record['cluster']='ADJECTIVE_MATCHED_AS_DISK_NOUN_BY_PRODUCTIVE_STEM' if group=='FALSE_HEATER' else 'HOST_CATEGORY_COMPOUND_MATCHED_AS_DISK_NOUN_BY_PRODUCTIVE_STEM'
      decisions.append(record)
      if is_defect:defects.append(record)
      if is_valid:valid.append(record)
      if is_terminal:terminal.append(record)
    counts=Counter(a['semantic_decision'] for a in decisions)
    assert counts=={'VALID_DISK':137,'FALSE_DISK_ALIAS':103,'NOT_APPLICABLE_TERMINAL':2}
    assert len(defects)==10 and len(valid)==137 and len(terminal)==2
    r.save(D/'DISK_ALIAS_SEMANTIC_DECISIONS.json',dict(status='COMPLETE',reviewed=242,unreviewed=0,counts=dict(counts),operator_review_required=0,rows=decisions,scope_note='FALSE_DISK_ALIAS is a lexical sold-object decision:103=10 actual persisted disk defects+93 non-disk outputs already protected. Do not count the93 as persisted corrections. NOT_APPLICABLE_TERMINAL includes one computed false disk on editorial input, separately flagged.'))
    r.save(D/'DISK_ALIAS_CONFIRMED_DEFECTS.json',dict(persisted_count=10,by_supplier=dict(Counter(a['entry']['supplier'] for a in defects)),rows=defects,terminal_computed_count=1,terminal=[a for a in terminal if a['terminal_computed_false_disk_alias']],terminal_eligibility_changes=0,repair_applied=False))
    r.save(D/'DISK_ALIAS_VALID_REGRESSION_COHORT.json',dict(count=137,by_supplier=dict(Counter(a['entry']['supplier'] for a in valid)),requirement='Future fix preserves entire current kind metadata,identity,commercial facts and all non-kind payload; corpus cases include legitimate adjective дисковая and noun круг.',rows=valid))
    r.save(D/'TERMINAL_DISK_ALIAS_DECISIONS.json',dict(candidates=2,computed_false_disk_alias=1,operator_review=0,rows=terminal,AMBIGUOUS=1,MULTI_VARIANT=1,outcome_delta=0,new_entities=0))
    heater=next(a for a in defects if a['review_group']=='FALSE_HEATER')
    r.save(D/'MSK324_EVIDENCE.json',dict(heater,confirmed=True,expected_concept='electrical plate heating element/spare component',exact_existing_ingestion_target='spare',registered_heating_element_taxon=False,spare_registration_note='spare is an existing source-ingestion kind emitted by source_evidence for mechanical spare components, not a PRODUCT_TAXONOMY registry member. No new subtype is invented. Existing registry has no explicit generic heating-element kind.',target_support=[dict(path='universal_supplier/adapters/ltt_uss_html.py',function='source_evidence',existing_spare_emitter='product_kind=spare/source=sold_title/confidence=HIGH')],source_packet=r.load(D/heater['source_packet']),db_mutation=False))
    clusters=[]
    for label in sorted({a['cluster'] for a in defects}):
      rows=[a for a in defects if a['cluster']==label]
      clusters.append(dict(name=label,count=len(rows),suppliers=dict(Counter(a['entry']['supplier'] for a in rows)),rows=[dict(supplier=a['entry']['supplier'],index=a['entry']['index'],stable_id=a['entry']['external_id'],current_kind=a['current_kind'],expected_concept=a['reason'],expected_existing_kind=a['expected_existing_kind'],matched_alias=a['current_kind']['matched_alias'],pattern=r'\bкруг\w*\b') for a in rows],unsafe='Productive circle stem conflates an adjective/host compound with a sold disk noun; category fallback has no sold-object corroboration.',safe_generic_fix_concept='Constrain circle noun readings to sold consumable function; reject geometry and host-category-only proof; resolve explicit component head to existing taxonomy, preserve true wheels and legitimate disc blades.',regression_risk='Do not remove all circle handling or all adjectives; preserve137 true disks and all protected cohorts; full future2633 impact replay required.'))
    r.save(D/'DISK_ALIAS_DEFECT_CLUSTERS.json',dict(persisted_clusters=clusters,persisted_cluster_count=2,terminal_diagnostic_cluster=dict(name='EDITORIAL_DISK_WORD_WITHOUT_SOLD_ENTITY',count=1,supplier='uss_stanko',index=225,stable_id=None,identity='terminal frozen URL, no persisted source identity',pattern=r'\bдиск\w*\b',matched_alias='дисковых',current_kind='disk/HIGH',expected='NOT_APPLICABLE_TERMINAL; retain AMBIGUOUS',repair_required_for_db=False),terminal_diagnostic_clusters_count=1))
    print('Reviewed242: valid137; actual persisted defects10; safe non-disk outputs93; terminal2 (computed false1); operator0',flush=True)

if __name__=='__main__':record()
