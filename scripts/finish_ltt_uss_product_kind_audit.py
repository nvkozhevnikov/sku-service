"""Serialize individually reviewed frozen-source decisions; audit artifacts only."""
from collections import Counter
from scripts.audit_ltt_uss_product_kind import OUT, r, o, q, MACHINE

# Explicit adjudications after reading all 392 candidates and the 20 terminal
# candidates, with deeper own-card inspection for each non-machine/uncertain row.
# These are review results, never classifier rules or an ingestion override.
REVIEW = {}
def record(supplier, index, decision, expected, sold, reason, cluster=None):
    assert (supplier, index) not in REVIEW
    REVIEW[supplier, index] = dict(semantic_decision=decision,
        expected_conceptual_kind=expected, actually_sold=sold,
        semantic_reason=reason, primary_cluster=cluster)

NA = 'COMPATIBILITY_PREPOSITION_NOT_STRIPPED'
HOST = 'HOST_MACHINE_TOKEN_OVERRIDES_SOLD_OBJECT'
CAT = 'CATEGORY_FALLBACK_OVERRIDES_UNRECOGNIZED_SOLD_OBJECT'
F = 'CONFIRMED_FALSE_MACHINE_FIXTURE'
C = 'CONFIRMED_FALSE_MACHINE_COMPONENT'
T = 'CONFIRMED_FALSE_MACHINE_TOOLING'
A = 'CONFIRMED_FALSE_MACHINE_ACCESSORY'
OTHER = 'OTHER_KIND_DEFECT'
OP = 'OPERATOR_REVIEW_REQUIRED'
for s, indices in [('ltt_msk',[88,129]),('ltt_sib',[914,915])]:
    for i in indices:
        record(s,i,F,'accessory/fixture','Пневматическая прижимная балка',
            'Own card describes attachment to the saw carriage and clamping/holding the workpiece; the beam does not perform the saw cutting operation.',NA)
record('ltt_msk',87,F,'accessory/fixture','Пневматический моноприжим',
    'Own purpose is fixing the workpiece in the cutting area; controlled from both sides of the carriage. Sold clamp, not its host saw.',CAT)
record('ltt_msk',302,F,'accessory/fixture','Откидной флажковый упор',
    'Own title identifies a flip stop mounted on the saw ruler; description lists compatible host saw series and manufacturers.',NA)
record('ltt_msk',344,F,'accessory/fixture','Поворотная электромагнитная плита',
    'Own description explicitly installs the plate on flat-knife sharpening machines; dimensions and three mounting holes describe the fixture.',CAT)
for i in [1143,1144]:
    record('ltt_sib',i,F,'accessory/fixture','Линейка-направляющая форматного станка',
        'The sold title is a longitudinal/profile or transverse telescopic ruler for the host saw, with length and installation dimensions, not the saw.',NA)
record('ltt_msk',40,T,'tooling/saw_blade','Пильный диск узла торцовки',
    'Own description: installed on the clean end-trimming unit; diameter 104 mm, bore 22 mm, thickness 2-3 mm, 30 teeth. A cutting blade, not a saw machine.',HOST)
record('ltt_msk',38,C,'component/cutting_attachment','Гильотинный узел обрезки кромки',
    'Own title explicitly specifies installation on an edgebander; own dimensions 110x70x34.3 mm, mass 0.5 kg and cutting stroke 25 mm describe its compact cutting unit.',CAT)
record('ltt_msk',300,C,'component/reading_lens','Линза для откидного упора',
    'Own title sells a lens for the saw flip stops; own description lists compatible E/F/Y/W/D saw series.',NA)
for i in [313,314]:
    record('ltt_msk',i,C,'component/chuck_nut','Гайка патрона ER20/ER25',
        'The sold item is the chuck nut. Own description specifies compatible milling/drilling chucks and host models; none of those machines is sold by this card.',CAT)
record('ltt_msk',315,C,'component/controller_interface','NC-интерфейсная карта с контроллером и кабелем',
    'Own description installs the card in a computer PCI slot, requiring Windows; package is one card, one controller and one cable. No machining centre is sold.',CAT)
for i in [203,240]:
    record('ltt_msk',i,A,'accessory/power_feeder','Автоподатчик заготовок',
        'Own purpose is feeding workpieces into the cutting area of separate woodworking machines; AF10 explicitly installs on those machines. Does not itself perform the host cutting operation.',CAT)
record('ltt_sib',938,A,'accessory/glue_supply_station','PUR-станция и клеевая голова',
    'Own purpose is melting and supplying PUR glue to application devices; package is station plus glue head, compatible with edgebanders of multiple manufacturers. Not a complete edgebander.',CAT)
record('ltt_sib',1159,C,'component/brush_strip','Бахрома для ЧПУ, рулон 1 м',
    'Own sold title identifies CNC fringe/brush strip, description gives a one-metre roll; own breadcrumb is spare parts for CNC routers. Not a router.',CAT)
record('ltt_sib',1161,C,'component/inductive_sensor','Индуктивный датчик LJ18A3-8-Z/BX',
    'Own description specifies a discrete 18x70 mm NPN 8 mm inductive sensor, 6-36 VDC/300 mA, not a milling machine.',CAT)
record('ltt_sib',1163,T,'tooling/knife','Нож копировально-фрезерного станка',
    'The sold title and description identify the knife for MFX9512-4. The description of making wooden handles refers to the host copying mill, not to a complete machine sold here.',CAT)
record('ltt_sib',955,T,'tooling/hand_edge_trimmer','Ручной продольный подрезатель кромки',
    'Own description explicitly says ручное приспособление для односторонней подрезки кромки 0,3-0,6 мм and resharpenable blade. A hand tool, not an edgebander.',CAT)
record('ltt_sib',960,T,'tooling/hand_edge_trimmer','Торцевой триммер JB90',
    'Own description identifies a trimmer that snips the edge tape, high-strength plastic body and replaceable/resharpenable knives; a cutting hand tool rather than an edgebanding machine.',CAT)
for s,i in [('ltt_msk',30),('ltt_msk',32),('ltt_sib',10),('ltt_sib',29)]:
    record(s,i,OTHER,'equipment/vacuum_pump','Вакуумный насос',
        'Own title and purpose sell a vacuum generating/pumping aggregate. Current milling_machine comes only from the category. A complete vacuum pump is not a milling machine; do not force it into spare/component without a taxonomy decision.',CAT)
record('ltt_msk',50,OTHER,'equipment/chiller','Чиллер CW-5000',
    'Own description sells a forced-cooling unit for the laser emitter: tank, refrigerant, temperature regulation/protection. A complete chiller is not the host laser machining machine; auxiliary-equipment taxonomy needs a deliberate choice.',CAT)
for i in [941,958]:
    record('ltt_sib',i,OP,'hand_tool_vs_complete_compact_trimmer_UNRESOLVED','Подрезатель кромки JB60/JB32S',
        'Own source describes edge cutting with replaceable knives (JB32S additionally 0.6 kg); likely hand tool, but does not explicitly establish manual actuation or complete powered/stationary status. Do not infer from model or price. Operator must settle the equipment/tool boundary.')

def proof(item, facts):
    return dict(facts=item['facts'],facts_sha256=item['facts_sha256'],
        evidence_path=facts['evidence_path'],response_sha256=item['response_sha256'],
        evidence_sha256=item['evidence_sha256'],original_observed_at=item['original_observed_at'],
        own_title=item['title'],own_description_excerpt=facts['source_description'][:2200],
        own_card_excerpt=facts['own_text'][:3600],description_is_not_classifier_input=True)

def decision(item, terminal=False):
    key=item['supplier'],item['index'];facts=r.load(OUT/item['facts'])
    assert r.digest((OUT/item['facts']).read_bytes())==item['facts_sha256']
    result=dict(item);result.pop('semantic_decision',None)
    if not terminal:
        verdict=REVIEW.get(key)
        if verdict is None:
            verdict=dict(semantic_decision='VALID_MACHINE',expected_conceptual_kind='complete_machine',
                actually_sold=item['title'],primary_cluster=None,
                semantic_reason='Individually reviewed own sold title and purpose: the card sells the complete processing/marking machine. Table, spindle, knives, tooling, pump, controls or other-machine/application wording describe its configuration or use, not a separate sold accessory.')
            if key==('ltt_sib',959):
                verdict['semantic_reason']='Own source describes a complete pneumatic edge-cutting device, actuated by pedal from a compressor, which can be fixed stationary; no host edgebanding machine is required. Preserve machine rather than demote solely because it is small.'
            if key==('ltt_sib',1085):
                verdict['semantic_reason']='Own H1 explicitly sells the MB1010HL thickness-planing machine, 1000 mm, with helical cutter shaft. Purpose section is empty; no accessory inference follows from that absence.'
            if key==('ltt_yug',541):
                verdict['semantic_reason']='Own title sells a complete CNC turn-mill machine LR-1530-6. Description has laser-application prose, a source-content inconsistency, but neither title nor own card sells a separate accessory. VALID_MACHINE is broad complete-machine status, not verification of every subtype/description claim.'
    elif item['index'] in {191,194,195,196,197}:
        verdict=dict(semantic_decision=T,expected_conceptual_kind='tooling/cutting_tools_multiple_variants',
            actually_sold='Наборы/сборные токарные резцы и сменные пластины',
            semantic_reason='Own heading and commercial article tables sell cutters, tool sets, blades and inserts. The adjective токарные/токарных selects lathe before the actual cutting-tool noun in ordered taxonomy; local override резец* does not cover резцы/резцов. Multi-row terminal remains non-persisted.',
            primary_cluster='LATHE_ADJECTIVE_PRECEDES_CUTTING_TOOL_TAXON')
    elif item['index'] in {204,205,206}:
        verdict=dict(semantic_decision='VALID_MACHINE',expected_conceptual_kind='complete_machine_multiple_variants',
            actually_sold=item['title'],primary_cluster=None,
            semantic_reason='Own title, purpose and model tables describe complete lathe variants. Kind lathe is semantically valid; multiple variants still prohibit creating a single source product. Existing MULTI_VARIANT terminal unchanged.')
    else:
        verdict=dict(semantic_decision=OP,expected_conceptual_kind='NON_PRODUCT_PAGE_KIND_APPLICABILITY_REVIEW',
            actually_sold='Category/reference page; no single sold source entity',primary_cluster=None,
            semantic_reason='Own heading/body is a machine category, overview or accuracy-reference page. Machine-family topic can be correct, but is not evidence of a complete sellable product. Decision vocabulary has no NOT_APPLICABLE choice; require operator applicability review, do not count as false-machine defect or approve persistence. Existing terminal unchanged.')
    result.update(verdict,review_method='INDIVIDUAL_FROZEN_OWN_SOURCE_SEMANTIC_REVIEW',
        evidence_class='PACKAGED-EVIDENCE',interpretation_class='INFERENCE',
        evidence=proof(item,facts),db_correction_candidate=not terminal and verdict['semantic_decision'] not in {'VALID_MACHINE',OP},
        terminal_outcome_changed=False if terminal else None)
    return result

def main():
    risks=r.load(OUT/'PRODUCT_KIND_RISK_CANDIDATES.json');terms=r.load(OUT/'TERMINAL_KIND_RISK_CANDIDATES.json')
    assert len(risks['rows'])==392 and len(terms['rows'])==20
    assert set(REVIEW)<= {(a['supplier'],a['index']) for a in risks['rows']}
    rows=[decision(a) for a in risks['rows']];terminal=[decision(a,True) for a in terms['rows']]
    defects=[a for a in rows if a['db_correction_candidate']];operators=[a for a in rows if a['semantic_decision']==OP]
    false=[a for a in defects if a['semantic_decision'].startswith('CONFIRMED_FALSE_MACHINE_')]
    assert len(rows)==392 and len(defects)==28 and len(false)==23 and len(operators)==2
    assert Counter(a['semantic_decision'] for a in rows)['VALID_MACHINE']==362
    for filename, payload in [('RISK_GENERATION_INPUT.json',risks),('TERMINAL_RISK_GENERATION_INPUT.json',terms)]:
        if not (OUT/filename).exists():r.save(OUT/filename,payload)
    risks['semantic_review_status']='COMPLETE_392_OF_392'
    terms['semantic_review_status']='COMPLETE_20_OF_20'
    for item,reviewed in zip(risks['rows'],rows):item['semantic_decision']=reviewed['semantic_decision']
    for item,reviewed in zip(terms['rows'],terminal):item['semantic_decision']=reviewed['semantic_decision']
    r.save(OUT/'PRODUCT_KIND_RISK_CANDIDATES.json',risks)
    r.save(OUT/'TERMINAL_KIND_RISK_CANDIDATES.json',terms)
    r.save(OUT/'PRODUCT_KIND_SEMANTIC_DECISIONS.json',dict(count=len(rows),all_generated_candidates_reviewed=True,
        decision_counts=dict(Counter(a['semantic_decision'] for a in rows)),
        scope='Product-kind risk only; not FULL_QA400 semantic acceptance or non-machine taxonomy certification',rows=rows))
    r.save(OUT/'PRODUCT_KIND_CONFIRMED_DEFECTS.json',dict(count=len(defects),confirmed_false_machine=len(false),
        other_kind_defect=len(defects)-len(false),db_correction_rows=len(defects),
        by_supplier=dict(Counter(a['supplier'] for a in defects)),rows=defects,
        source_ids_remain_independent=True,correction_applied=False))
    r.save(OUT/'PRODUCT_KIND_OPERATOR_REVIEW.json',dict(count=len(operators),rows=operators,
        excluded_from_confirmed_and_db_correction_counts=True))
    r.save(OUT/'TERMINAL_KIND_SEMANTIC_DECISIONS.json',dict(count=len(terminal),
        decision_counts=dict(Counter(a['semantic_decision'] for a in terminal)),
        db_correction_rows=0,terminal_outcomes_unchanged=True,rows=terminal))
    r.save(OUT/'SEMANTIC_REVIEW_MANIFEST.json',dict(method='Serial individual reading of candidate own title/purpose; deeper own-card/source inspection for exceptions',
        reviewed_by_supplier=dict(Counter(a['supplier'] for a in rows)),
        persisted_reviewed_keys=[[a['supplier'],a['index']] for a in rows],
        terminal_reviewed_keys=[[a['supplier'],a['index']] for a in terminal],
        risk_input_sha256=r.digest((OUT/'PRODUCT_KIND_RISK_CANDIDATES.json').read_bytes()),
        terminal_input_sha256=r.digest((OUT/'TERMINAL_KIND_RISK_CANDIDATES.json').read_bytes()),
        original_generation_sha256=r.digest((OUT/'RISK_GENERATION_INPUT.json').read_bytes()),
        original_terminal_generation_sha256=r.digest((OUT/'TERMINAL_RISK_GENERATION_INPUT.json').read_bytes()),
        unreviewed_candidates=0,auto_accept_by_classifier_confidence=False))
    clusters=[]
    descriptions={
      NA:('Compatibility markers omit на, so the host saw stays in the sold head and machine_name/HIGH wins.',
          'Recognize verified sold fixture/component and its host relation, including на, while retaining complete-machine head precedence.',
          'Global phrase replacement would downgrade complete machines mentioning installation or a second machine.'),
      HOST:('Host-machine genitive occurs in title without a recognized relation boundary; machine_name/HIGH overrides the sold saw blade.',
          'Resolve the sold cutting object separately from its host unit using own dimensions/teeth and title structure.',
          'A complete saw also legitimately mentions disks/teeth; title substring alone is unsafe.'),
      CAT:('No sold-object taxon found after compatibility handling; machine-family category/MEDIUM supplies the kind despite own object evidence.',
          'Add source-evidenced sold-object concepts or fail closed before machine category fallback; separately model complete auxiliary pumps/chillers.',
          'Unconditional category suppression would lose legitimate machines with model-only headings; pumps are not automatically spare parts.'),
      'LATHE_ADJECTIVE_PRECEDES_CUTTING_TOOL_TAXON':('Ordered lathe alias токарн* wins name_head/HIGH over tool; local резец* override misses резцы/резцов.',
          'Resolve complete-machine versus cutter/set sold noun and inflected forms before allowing lathe adjective to establish machine kind.',
          'Complete lathes and sharpening machines legitimately mention cutters; preserve own complete-machine evidence.')}
    for name in [NA,HOST,CAT,'LATHE_ADJECTIVE_PRECEDES_CUTTING_TOOL_TAXON']:
        p=[a for a in defects if a['primary_cluster']==name]
        t=[a for a in terminal if a['primary_cluster']==name]
        cause,fix,risk=descriptions[name]
        members=p+t
        clusters.append(dict(pattern=name,persisted_confirmed_count=len(p),terminal_confirmed_count=len(t),
            confirmed_count=len(members),suppliers=sorted({a['supplier'] for a in members}),
            rows=[dict(supplier=a['supplier'],index=a['index'],external_id=a['external_id'],persisted=a in p,
                decision=a['semantic_decision'],current_trace=a['raw_enrichment_evidence']['product_kind'],
                evidence_sha256=a['evidence_sha256']) for a in members],
            verified_technical_pattern=cause,technical_claim_class='REPOSITORY-VERIFIED',
            safe_generic_correction_candidate=fix,correction_candidate_class='INFERENCE_NOT_IMPLEMENTED',
            risk_of_false_downgrade=risk,code_evidence='CLASSIFIER_ARCHITECTURE.json'))
    assert sum(a['persisted_confirmed_count'] for a in clusters)==28
    r.save(OUT/'PRODUCT_KIND_DEFECT_CLUSTERS.json',dict(persisted_clusters=3,terminal_only_clusters=1,
        total_clusters=4,persisted_confirmed_count=28,terminal_confirmed_count=5,clusters=clusters,
        legitimate_machine_safety_examples=[dict(supplier=s,index=i) for s,i in [
            ('ltt_msk',64),('ltt_msk',358),('ltt_msk',359),('ltt_sib',959),('ltt_yug',386),('uss_stanko',350)]],
        future_fix_must_preserve_all_362_valid_candidates=True))
    known=next(a for a in rows if a['supplier']=='ltt_msk' and a['index']==129)
    facts=r.load(OUT/known['facts'])
    assert known['external_id']=='1910' and known['semantic_decision']==F
    assert known['price']['current_price']=='42000' and known['price']['currency']=='RUB' and known['availability']=='OutOfStock'
    r.save(OUT/'ROW129_EVIDENCE.json',dict(**known,full_own_description=facts['source_description'],
        operator_confirmed_expected_kind='accessory/fixture',current_kind_preserved=True,
        source_id_price_stock_hash_time_preserved=True,related_msk_row88_id241_not_merged=True))
    # Original generated candidates are preserved separately; final candidate
    # files also carry the reviewed verdict for convenient operator navigation.
    r.save(OUT/'SWEEP_COUNTS.json',dict(persisted_rows_inventoried=2453,product_kind_risk_candidates=392,
        semantically_reviewed_candidates=392,confirmed_false_machine=23,other_kind_defect=5,
        confirmed_defects=28,valid_machine=362,operator_review_required=2,
        affected_suppliers=['ltt_msk','ltt_sib'],persisted_defect_clusters=3,
        terminal_computed=180,terminal_candidates=20,terminal_reviewed=20,
        terminal_false_machine=5,terminal_valid_machine=3,terminal_operator_applicability_review=12,
        terminal_only_defect_clusters=1,db_correction_rows=28))
    print(r.load(OUT/'SWEEP_COUNTS.json'))

if __name__=='__main__':main()
