"""Read-only post-failure diagnostic, NOT an accepted impact gate or RUN2."""
from dataclasses import asdict
from collections import Counter
import copy
from scripts.audit_ltt_uss_product_kind import OUT as AUDIT, r, o, q
from universal_supplier.ltt_uss_capture import _card_gate

P=r.BASE/'PRODUCT_KIND_BATCH_FIX_2026-10-16'

def main():
    assert r.load(P/'TEST_GATE_BLOCKER.json')['status']=='FAILED_STOP_BEFORE_DB'
    pre=r.load(P/'PREFLIGHT.json');proposal=r.load(P/'CODE_PROPOSAL.json')
    assert r.code_hashes()==proposal['new']
    old=r.load(AUDIT/'PRODUCT_KIND_INVENTORY.json')['rows']
    terminals=r.load(AUDIT/'TERMINAL_KIND_INVENTORY.json')['rows']
    targets={(a['supplier'],a['index']):a for a in r.load(AUDIT/'PRODUCT_KIND_CONFIRMED_DEFECTS.json')['rows']}
    valid={(a['supplier'],a['index']) for a in r.load(AUDIT/'PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'}
    expected_terminal={('uss_stanko',i) for i in [191,194,195,196,197]}
    changed=set();tchanged=set();rows=[];kind_errors=[];payload_errors=[];outcome_errors=[];terminal_ids=[]
    with q.guard.guard() as network:
      for item in old+terminals:
        key=item['supplier'],item['index'];facts=r.load(AUDIT/item['facts'])
        assert r.digest((AUDIT/item['facts']).read_bytes())==item['facts_sha256']
        body=(o.ROOT/facts['evidence_path']).read_bytes()
        assert r.digest(body)==item['evidence_sha256']
        journal=r.load(o.ROOT/item['journal']);meta=journal['result']
        product=r.parse(item['supplier'],body,meta['final_url']);new=asdict(product)
        before=item['raw_enrichment_evidence']['product_kind'];after=new['enrichment_evidence']['product_kind']
        persisted=item['outcome']=='PERSISTED';different=before!=after
        if different:(changed if persisted else tchanged).add(key)
        status,diagnostics=_card_gate(item['supplier'],body.decode('utf8'),meta['final_url'])
        outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diagnostics:outcome='MULTI_VARIANT'
        if outcome!=item['outcome']:outcome_errors.append(dict(key=key,old=item['outcome'],new=outcome))
        if persisted:
            record=r.load(o.ROOT/item['db_evidence']['record'])
            assert r.digest((o.ROOT/item['db_evidence']['record']).read_bytes())==item['db_evidence']['sha256']
            reverted=copy.deepcopy(new);reverted['enrichment_evidence']['product_kind']=before
            if r.encoded(reverted)!=r.encoded(record['parsed_product']):payload_errors.append(key)
        else:
            if new['site_internal_id']!=item['external_id']:terminal_ids.append(key)
            assert new['price']==item['price'] and new['availability']==item['availability']
        if key in targets:
            expected=targets[key]['expected_conceptual_kind'].split('/')[-1]
            if after['product_kind']!=expected:kind_errors.append(dict(key=key,expected=expected,actual=after))
        if key in expected_terminal and after['product_kind']!='cutting_tool':kind_errors.append(dict(key=key,expected='cutting_tool',actual=after))
        rows.append(dict(supplier=item['supplier'],index=item['index'],external_id=item['external_id'],title=item['title'],
            old_kind=before,new_kind=after,kind_changed=different,persisted=persisted,
            old_outcome=item['outcome'],new_outcome=outcome,evidence_sha256=item['evidence_sha256'],response_sha256=item['response_sha256'],
            target_concept=targets[key]['expected_conceptual_kind'] if key in targets else None,
            root_cause_cluster=targets[key]['primary_cluster'] if key in targets else None,
            source_product_id=item['db_evidence'].get('source_product_id'),offer_id=item['db_evidence'].get('offer_id'),
            db_correction_applied=False))
        if len(rows)%250==0:
            r.save(P/'DIAGNOSTIC_PROGRESS.json',dict(checked=len(rows),total=2633,db_started=False))
            print('READONLY IMPACT DIAGNOSTIC',len(rows),'/2633',flush=True)
    additional=changed-set(targets);additional_t=tchanged-expected_terminal
    result=dict(status='DIAGNOSTIC_ONLY_TEST_GATE_FAILED_NOT_ACCEPTED',persisted_checked=2453,terminal_computed_checked=180,
        category_yug_not_computed=1,total_frozen_scope=2634,expected_persisted=28,changed_persisted=len(changed),
        changed_persisted_keys=sorted(changed),changed_terminal=len(tchanged),changed_terminal_keys=sorted(tchanged),
        additional_persisted=sorted(additional),additional_terminal=sorted(additional_t),
        missing_persisted=sorted(set(targets)-changed),missing_terminal=sorted(expected_terminal-tchanged),
        valid_machine_changes=sorted(valid&changed),operator_rows_changes=sorted({('ltt_sib',941),('ltt_sib',958)}&changed),
        expected_kind_mismatches=kind_errors,non_kind_persisted_payload_changes=payload_errors,
        terminal_outcome_changes=outcome_errors,terminal_identity_changes=terminal_ids,
        gate_accepted=False,db_started=False,db_writes=0,supplier_http=0,network=network,rows=rows,checked_at=r.now())
    r.save(P/'PRODUCT_KIND_POSTFIX_IMPACT.json',result)
    manifest=[a for a in rows if (a['supplier'],a['index']) in targets]
    r.save(P/'PROPOSED_28_ROW_CORRECTION_MANIFEST.json',dict(count=len(manifest),status='PROPOSED_ONLY_NOT_APPLIED',rows=manifest))
    print({k:v for k,v in result.items() if k not in {'rows','network'}},flush=True)

if __name__=='__main__':main()
