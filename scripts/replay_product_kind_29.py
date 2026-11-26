"""Complete frozen parser/commercial/outcome replay, no DB or transport."""
from dataclasses import asdict
from collections import Counter
import copy
from scripts.audit_ltt_uss_product_kind import OUT as AUDIT, r, o, q
from scripts.prepare_product_kind_29 import P
from universal_supplier.ltt_uss_capture import _card_gate

def main():
    assert not (P/'FULL_REPLAY.json').exists()
    targets={(a['supplier'],a['index']):a for a in r.load(P/'AUTHORIZED_29_ROWS.json')['rows']}
    inventory=r.load(AUDIT/'PRODUCT_KIND_INVENTORY.json')['rows']+r.load(AUDIT/'TERMINAL_KIND_INVENTORY.json')['rows']
    valid={(a['supplier'],a['index']) for a in r.load(AUDIT/'PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'}
    term_expected={('uss_stanko',i) for i in [191,194,195,196,197]}
    changed=set();terminal_changed=set();rows=[];manifest=[];plan=copy.deepcopy(r.load(q.PLAN));kinds={}
    def canonical_equal(a,b):return r.encoded(a)==r.encoded(b)
    with q.guard.guard() as network:
      for item in inventory:
        key=item['supplier'],item['index'];facts=r.load(AUDIT/item['facts'])
        assert r.digest((AUDIT/item['facts']).read_bytes())==item['facts_sha256']
        body=(o.ROOT/facts['evidence_path']).read_bytes();assert r.digest(body)==item['evidence_sha256']
        path=o.ROOT/item['journal'];assert r.digest(path.read_bytes())==item['journal_sha256']
        journal=r.load(path);meta=journal['result']
        response=r.load(r.OUT/meta['response_record']);raw=(r.OUT/response['raw_path']).read_bytes()
        assert r.digest(raw)==item['response_sha256']==response['response_sha256']
        assert o.sanitise_for_charset(raw,meta['charset'])==body
        product=r.parse(item['supplier'],body,meta['final_url']);new=asdict(product)
        before=item['raw_enrichment_evidence']['product_kind'];after=new['enrichment_evidence']['product_kind']
        persisted=item['outcome']=='PERSISTED';different=before!=after
        if different:(changed if persisted else terminal_changed).add(key)
        status,diag=_card_gate(item['supplier'],raw.decode(meta['charset'] or 'utf8','replace'),meta['final_url'])
        outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diag:outcome='MULTI_VARIANT'
        assert outcome==item['outcome'] and status.value==meta['status'] and list(diag)==meta['diagnostics'],('OUTCOME_DRIFT',key)
        assert new['name']==item['title'] and new['site_internal_id']==item['external_id'],('IDENTITY_TITLE_DRIFT',key)
        # Comparison normalization only: JSON represents tuple as list and
        # Decimal as its original string. No price values or raw text altered.
        assert canonical_equal(new['price'],item['price']) and new['availability']==item['availability'],('COMMERCIAL_DRIFT',key)
        reverted=copy.deepcopy(new);reverted['enrichment_evidence']['product_kind']=before
        assert canonical_equal(reverted['enrichment_evidence'],item['raw_enrichment_evidence']),('ENRICHMENT_DRIFT',key)
        if persisted:
            record=r.load(o.ROOT/item['db_evidence']['record'])
            assert r.digest((o.ROOT/item['db_evidence']['record']).read_bytes())==item['db_evidence']['sha256']
            assert canonical_equal(reverted,record['parsed_product']),('NON_KIND_PAYLOAD_DRIFT',key)
        if key in targets:
            expected=targets[key]['expected_conceptual_kind'].split('/')[-1]
            assert after['product_kind']==expected and after['confidence']=='HIGH',('TARGET_KIND_MISMATCH',key)
            entry=next(e for e in plan['rows'] if (e['supplier'],e['index'])==key)
            revised=copy.deepcopy(journal);revised['product_payload_sha256']=r.digest(r.encoded(new))
            revised['reviewed_kind_correction']=dict(original_journal=entry['journal'],original_journal_sha256=entry['journal_sha256'],
                old_kind=before,new_kind=after,response_sha256=item['response_sha256'],evidence_sha256=item['evidence_sha256'],no_new_supplier_http=True)
            dest=P/'REVISED_ROWS'/item['supplier']/path.name;r.save(dest,revised)
            entry.update(journal=str(dest.relative_to(o.ROOT)),journal_sha256=r.digest(dest.read_bytes()))
            manifest.append(dict(supplier=item['supplier'],index=item['index'],external_id=item['external_id'],url=item['url'],title=item['title'],
                old_kind=before,new_kind=after,expected_concept=targets[key]['expected_conceptual_kind'],cluster=targets[key]['primary_cluster'],
                response_sha256=item['response_sha256'],evidence_sha256=item['evidence_sha256'],original_observed_at=item['original_observed_at'],
                source_product_id=item['db_evidence']['source_product_id'],offer_id=item['db_evidence']['offer_id'],
                original_db_record=item['db_evidence']['record'],original_db_record_sha256=item['db_evidence']['sha256'],
                revised_journal=entry['journal'],revised_journal_sha256=entry['journal_sha256']))
        if key in term_expected:assert after['product_kind']=='cutting_tool' and after['confidence']=='HIGH'
        if key in valid or key in {('ltt_sib',941),('ltt_sib',958)}:assert not different,('PROTECTED_KIND_DRIFT',key)
        kinds[str(key)]=after
        r.save(P/'PARSED_ROWS'/item['supplier']/f"{item['index']:06d}.json",dict(inventory=item,parsed_product=new))
        rows.append(dict(supplier=item['supplier'],index=item['index'],persisted=persisted,old_kind=before,new_kind=after,
            kind_changed=different,outcome=outcome,commercial_identity_title_unchanged=True,evidence_sha256=item['evidence_sha256']))
        if len(rows)%200==0:
            r.save(P/'REPLAY_PROGRESS.json',dict(checked=len(rows),total=2634,db_started=False));print('FULL FROZEN REPLAY',len(rows),'/2634',flush=True)
      category=next(e for e in plan['rows'] if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE')
      path=o.ROOT/category['journal'];assert r.digest(path.read_bytes())==category['journal_sha256']
      assert o.extract(r.load(path))==('NON_PRODUCT_CATEGORY_PAGE',None,None)
    assert len(rows)==2633 and len(plan['rows'])==2634 and changed==set(targets) and terminal_changed==term_expected
    assert len(manifest)==29 and Counter(a['supplier'] for a in manifest)==dict(ltt_msk=17,ltt_sib=12)
    assert next(a for a in manifest if a['index']==318 and a['supplier']=='ltt_msk')['source_product_id']!=next(a for a in manifest if a['index']==40 and a['supplier']=='ltt_msk')['source_product_id']
    r.save(P/'PRODUCT_KIND_29_MANIFEST.json',dict(count=29,by_supplier=dict(ltt_msk=17,ltt_sib=12),rows=manifest))
    r.save(P/'EFFECTIVE_ROW_PLAN.json',plan)
    result=dict(status='FULL_PARSER_COMMERCIAL_OUTCOME_REPLAY_PASS',scope=2634,persisted=2453,terminal_computed=180,category=1,
        changed_persisted=29,changed_terminal=5,additional_persisted=0,additional_terminal=0,
        identity_delta=0,title_delta=0,price_delta=0,currency_delta=0,availability_delta=0,non_kind_payload_delta=0,terminal_outcome_delta=0,
        valid_machine_regressions=0,valid_machine_cohort=362,operator_review_rows_changed=0,network=network,rows=rows,checked_at=r.now())
    r.save(P/'FULL_REPLAY.json',result);print({k:v for k,v in result.items() if k not in {'rows','network'}},flush=True)

if __name__=='__main__':main()
