"""Assess protected output risk and future concept without implementing a fix."""
from scripts.audit_disk_alias import *

def main():
    inputs=r.load(D/'ALL_INPUTS.json')['rows'];allrows={(a['entry']['supplier'],a['entry']['index']):a for a in inputs}
    decisions=r.load(D/'DISK_ALIAS_SEMANTIC_DECISIONS.json')['rows'];hitkeys={(a['entry']['supplier'],a['entry']['index']) for a in decisions}
    defects=r.load(D/'DISK_ALIAS_CONFIRMED_DEFECTS.json')['rows'];bad={(a['entry']['supplier'],a['entry']['index']) for a in defects}
    cohorts={
      'PREVIOUS_INVERSE97':[(a['supplier'],a['index']) for a in r.load(B/'INVERSE_KIND_97_MANIFEST.json')['rows']],
      'PREVIOUS_KIND29':[(a['supplier'],a['index']) for a in r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows']],
      'TERMINAL_INVERSE4':[('uss_stanko',i) for i in [50,51,182,221]],
      'PREVIOUS_TERMINAL5':[('uss_stanko',i) for i in [191,194,195,196,197]],
      'VALID_MACHINE362':[(a['supplier'],a['index']) for a in r.load(r.BASE/'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'],
      'OPERATOR106':[(a['supplier'],a['index']) for a in r.load(AUDIT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='OPERATOR_REVIEW_REQUIRED']}
    checked={}
    for label,keys in cohorts.items():
      rows=[]
      assert not set(keys)&bad,('CONFIRMED_DEFECT_OVERLAPS_PROTECTED',label)
      for key in keys:
        a=allrows[key];saved=r.load(B/'PARSED_ROWS'/key[0]/f'{key[1]:06d}.json')
        assert a['current_kind']==saved['parsed_product']['enrichment_evidence']['product_kind'] and a['payload_sha256']==r.digest(r.encoded(saved['parsed_product']))
        rows.append(dict(supplier=key[0],index=key[1],current_kind=a['current_kind'],outcome=a['outcome'],payload_sha256=a['payload_sha256'],any_disk_alias_input_match=key in hitkeys,title_category_prior=a['title_category_prior'],risk='DIRECT_SHARED_ALIAS_EXPOSURE_REQUIRE_EXACT_FUTURE_OUTPUT' if key in hitkeys else 'NO_CURRENT_DISK_ALIAS_INPUT_MATCH_STILL_REQUIRE_FULL_REGRESSION',current_baseline_unchanged=True))
      checked[label]=dict(status='ASSESSED_NOT_IMPLEMENTED',count=len(rows),disk_alias_intersections=sum(a['any_disk_alias_input_match'] for a in rows),confirmed_defect_overlap=0,rows=rows,requirement='Future proposal must preserve entire accepted payload/outcome and operator mapping; this audit has no proposed implementation and cannot verify future zero impact.')
    assert {k:v['count'] for k,v in checked.items()}=={'PREVIOUS_INVERSE97':97,'PREVIOUS_KIND29':29,'TERMINAL_INVERSE4':4,'PREVIOUS_TERMINAL5':5,'VALID_MACHINE362':362,'OPERATOR106':106}
    r.save(D/'PROTECTED_COHORTS_RISK.json',dict(status='ASSESSED',cohorts=checked,all_current_outputs_equal_accepted=True,classification_code_changed=False,fix_implemented=False,future_fix_regression_verified=False))
    plan=dict(status='CONCEPT_ONLY_NOT_IMPLEMENTED',scope='Actual disk aliases only; no reopen of unrelated kinds',generic_steps=[
      'Recognize disk as sold object using noun morphology plus own cutting/abrasive/polishing function; retain круг/круги and inflected noun forms rather than unrestricted круг followed by arbitrary word suffix.',
      'Treat круглый/круглая/круглое and host compounds круглопильный/круглопалочный as shape,workpiece or application evidence; they cannot independently prove a sold disk in name or category.',
      'Retain legitimate adjective constructions пила дисковая/дисковая пила and actual пильный диск; current137 true disks include129 disc-blade names and8 circle-noun wheels. Do not reject all adjectives or delete all circle aliases.',
      'A host-category match must not assign consumable disk to an explicitly sold heater,feed wheel,laser marker,knife or bushing. Resolve functional sold head to an existing kind when supported; otherwise remain unknown/operator rather than falling through to host-machine type.',
      'Use existing ingestion spare for heating/mechanical spare components,existing accessory for laser marker and existing knife for rectangular knives; no new one-row taxon and no stable-ID exceptions. spare is emitted by adapter but absent from core registry; future implementation must preserve this known distinction rather than invent a registered heater kind.',
      'Keep all original identity,title,commercial data and outcomes. Editorial terminal should stay non-persistable; this task does not authorize a terminal semantic repair.'],
      protected_true_disk_count=137,protected_cohorts=list(checked),actual_stem_risks=['Own adjective круглый -> heater disk/HIGH','Host-category круглопалочных/круглопильных -> components disk/MEDIUM'],
      preserved_actual_constructions=['Пильный диск','Пила дисковая','Дисковая пила','Полировальный круг','Круг заточной'],
      cbn_sdc='Both real configured aliases have0 hits in this corpus. Keep semantics unchanged; no corpus acceptance claim. Future focused synthetic regression required if touched.',
      proposed_correction_scope=dict(persisted=10,by_supplier={'ltt_msk':5,'ltt_sib':5},target_counts=dict(Counter(a['expected_existing_kind'] for a in defects)),not_authorized_for_apply=True),
      regression_risks=['Shared classifier has consumers beyond LTT/USS; scope of their outputs is not audited or modified here.','Narrowing a disk category fallback can expose another category taxon,including host machine. A lexical removal alone is unsafe.','Adapter currently suppresses four disk priors to spare; complete-machine rule suppresses USS221 disk prior. Final metadata and terminal outcome must remain exact.','8 true wheels rely on circle-noun handling;129 true blades use disk/adjective readings.','No tested implementation exists; next scoped fix needs full2633 payload impact with exact allowlist and protected cohorts plus normal tests before any SQL authorization.'])
    r.save(D/'SAFE_GENERIC_FIX_CONCEPT.json',plan)
    rules=r.load(D/'DISK_ALIAS_RULES.json');rules.update(normalization=dict(function='sterbrust_matching.normalization.normalize_text',steps=['None -> empty','Unicode NFKC','lowercase','ё -> е','Unicode dashes -> ASCII hyphen','collapse whitespace;strip']),source_calls=[dict(path='scripts/run_ltt_uss_live_canary.py',line=30,function='parse'),dict(path='universal_supplier/adapters/ltt.py',line=223,function='source_evidence with scoped own_description'),dict(path='universal_supplier/adapters/ltt_yug.py',line=176,function='source_evidence without own_description'),dict(path='universal_supplier/adapters/uss_stanko.py',line=477,function='source_evidence without own_description'),dict(path='universal_supplier/adapters/ltt_uss_html.py',line=77,function='classify_product_kind')],trace_scope='Observer wraps the single source-enrichment parser call; o.extract validation runs outside observer because it additionally classifies a derived ProductCard. Primary fields contain all name/category inputs; description-only matches are explicit inventory-only hits.',trace_current_outputs_unmodified=2633)
    r.save(D/'DISK_ALIAS_RULES.json',rules)
    summary=dict(persisted_inputs=2453,terminal_inputs=180,category_pages_separate=1,candidates=242,persisted_candidates=240,terminal_candidates=2,currently_disk=148,persisted_currently_disk=147,valid_disk=137,confirmed_persisted_false=10,non_disk_winners_with_context_only_hits=93,semantic_false_alias_label_count=103,terminal_false_computed_disk=1,operator_review=0,unreviewed=0,alias_pattern_row_counts={p:sum(any(m['pattern']==p for ms in a['input_matches'].values() for m in ms) for a in decisions) for p in PATTERNS},description_only_candidates=sum(bool(a['input_matches']['own_description']) and not (a['input_matches']['name'] or a['input_matches']['category']) for a in decisions),disk_selected_then_overridden=sum(a['disk_selected_then_overridden'] for a in decisions),adapter_disk_to_spare=4,complete_machine_disk_override=1,higher_priority_taxon_winner_in_executed_scan=0,preferred_machine_branch_bypasses_disk=sum(a['title_category_prior']['source']=='machine_name' for a in decisions))
    r.save(D/'SCOPE_SUMMARY.json',summary)
    print('Protection risks', {k:(v['count'],v['disk_alias_intersections']) for k,v in checked.items()},flush=True)
    print('Concept only; no source/DB repair. Summary',summary,flush=True)

if __name__=='__main__':main()
