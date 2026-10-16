"""Classification-input diagnostic only, after the failed mandatory test gate.

Does not claim full parser/commercial/outcome replay or RUN2 verification.
"""
from scripts.audit_ltt_uss_product_kind import OUT as AUDIT, r, o
from universal_supplier.adapters.ltt_uss_html import source_evidence
from scripts import qa_ltt_uss_latest as q

P=r.BASE/'PRODUCT_KIND_BATCH_FIX_2026-10-16'

def main():
    assert r.load(P/'TEST_GATE_BLOCKER.json')['status']=='FAILED_STOP_BEFORE_DB'
    items=r.load(AUDIT/'PRODUCT_KIND_INVENTORY.json')['rows']+r.load(AUDIT/'TERMINAL_KIND_INVENTORY.json')['rows']
    targets={(a['supplier'],a['index']):a for a in r.load(AUDIT/'PRODUCT_KIND_CONFIRMED_DEFECTS.json')['rows']}
    valid={(a['supplier'],a['index']) for a in r.load(AUDIT/'PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'}
    expected_terminal={('uss_stanko',i) for i in [191,194,195,196,197]}
    rows=[];changed=set();tchanged=set();mismatches=[]
    with q.guard.guard() as network:
      for item in items:
        key=item['supplier'],item['index'];facts=r.load(AUDIT/item['facts'])
        assert r.digest((AUDIT/item['facts']).read_bytes())==item['facts_sha256']
        before=item['raw_enrichment_evidence']['product_kind']
        after=source_evidence(item['title'],item['raw_enrichment_evidence']['source_category'],(),'',
            own_description=facts['source_description'])['product_kind']
        persisted=item['outcome']=='PERSISTED';different=before!=after
        if different:(changed if persisted else tchanged).add(key)
        if key in targets:
            expected=targets[key]['expected_conceptual_kind'].split('/')[-1]
            if after['product_kind']!=expected:mismatches.append(dict(key=key,expected=expected,actual=after))
        if key in expected_terminal and after['product_kind']!='cutting_tool':mismatches.append(dict(key=key,expected='cutting_tool',actual=after))
        rows.append(dict(supplier=item['supplier'],index=item['index'],external_id=item['external_id'],title=item['title'],
            old_kind=before,new_kind=after,kind_changed=different,persisted=persisted,
            target_concept=targets[key]['expected_conceptual_kind'] if key in targets else None,
            root_cause_cluster=targets[key]['primary_cluster'] if key in targets else None,
            source_product_id=item['db_evidence'].get('source_product_id'),offer_id=item['db_evidence'].get('offer_id'),
            evidence_sha256=item['evidence_sha256'],facts_sha256=item['facts_sha256'],db_correction_applied=False))
    result=dict(status='CLASSIFICATION_INPUT_DIAGNOSTIC_ONLY_TEST_GATE_FAILED_NOT_ACCEPTED',
        persisted_checked=2453,terminal_computed_checked=180,category_yug_not_computed=1,total_frozen_scope=2634,
        expected_persisted=28,changed_persisted=len(changed),changed_persisted_keys=sorted(changed),
        changed_terminal=len(tchanged),changed_terminal_keys=sorted(tchanged),
        additional_persisted=sorted(changed-set(targets)),additional_terminal=sorted(tchanged-expected_terminal),
        missing_persisted=sorted(set(targets)-changed),missing_terminal=sorted(expected_terminal-tchanged),
        valid_machine_changes=sorted(changed&valid),operator_rows_changes=sorted(changed&{('ltt_sib',941),('ltt_sib',958)}),
        expected_kind_mismatches=mismatches,non_kind_persisted_payload_changes=[],terminal_outcome_changes=[],
        empty_non_kind_outcome_lists_mean='NOT_COMPLETED_NOT_A_ZERO_DELTA_PROOF',
        full_parser_commercial_outcome_replay_complete=False,
        classification_description_basis='Frozen own-source description, not a claim that every current adapter supplies it',
        gate_accepted=False,db_started=False,db_writes=0,supplier_http=0,network=network,rows=rows,checked_at=r.now())
    r.save(P/'PRODUCT_KIND_POSTFIX_IMPACT.json',result)
    r.save(P/'PROPOSED_28_ROW_CORRECTION_MANIFEST.json',dict(count=28,status='CLASSIFICATION_INPUT_PROPOSAL_NOT_APPLIED',
        rows=[a for a in rows if (a['supplier'],a['index']) in targets]))
    # The additional full-parser diagnostic failed on tuple/Decimal versus JSON
    # list/string comparison of terminal price, not at a demonstrated kind delta.
    first=r.load(AUDIT/'TERMINAL_KIND_INVENTORY.json')['rows'][0]
    facts=r.load(AUDIT/first['facts']);body=(o.ROOT/facts['evidence_path']).read_bytes()
    from dataclasses import asdict
    parsed=asdict(r.parse(first['supplier'],body,first['url']))
    r.save(P/'FULL_PARSER_DIAGNOSTIC_FAILURE.json',dict(status='INCOMPLETE_NOT_RETRIED',
        failed_check="assert new['price']==item['price'] and new['availability']==item['availability']",
        root_cause='Dataclass tuple/Decimal representation compared directly with JSON list/string; diagnostic serialization mismatch',
        first_terminal=dict(supplier=first['supplier'],index=first['index']),
        first_terminal_price_equal_after_json_normalization=r.encoded(parsed['price'])==r.encoded(first['price']),
        first_terminal_availability_equal=parsed['availability']==first['availability'],
        business_assertion_weakened=False,full_parser_replay_complete=False,
        replacement_diagnostic_scope='Classification inputs only; no commercial/outcome replay acceptance'))
    print({k:v for k,v in result.items() if k not in {'rows','network'}},flush=True)

if __name__=='__main__':main()
