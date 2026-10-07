"""Serialize persisted decisions; no matching, selection, supplier HTTP or SQL."""
import csv,hashlib,json
from pathlib import Path
from xml.etree import ElementTree as ET
from universal_supplier.release_export import xml_bytes,tables,summarize,validate

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
OUT=ROOT/'release/KAMI_FEATURE_RC_2026-10-06'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    raise RuntimeError('Superseded advisory28/32-row assembler disabled. Use finalize_kami_release.py with fresh persisted-canonical receipt; no hidden reselection.')
    matching=BASE/'MATCHING/MATCHING_ACCEPTED.json'
    inputs=BASE/'SELECTION_RECONCILIATION_2026-10-06/EXPORT_INPUT_ROWS.json';selection=BASE/'SELECTION_RECONCILIATION_2026-10-06/SELECTION_PERSISTED.json'
    rows=json.loads(inputs.read_bytes())['rows'];decisions=json.loads(matching.read_bytes())['rows']
    assert len(rows)==len(decisions)==9671
    assert [(r['source'],r['external_id'],r['classification'],r.get('sterbrust_product_id')) for r in rows]==[
        (r['source'],r['external_id'],r['classification'],r.get('sterbrust_product_id')) for r in decisions]
    state=json.loads(selection.read_bytes())
    assert state['status']=='LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION'
    from scripts.reconcile_kami_selection import connection, recompute, verify_set
    from scripts.verify_kami_canary import digest
    with connection() as conn, conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        _, _, expected, diff=recompute(conn)
        assert all(d['action']=='KEEP' for d in diff)
        verify_set(conn,expected)
        actual=digest([r[0] for r in conn.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')])
        assert actual==state['persisted_table_sha256']
    payload_path=ROOT/'release/FINAL_RC_2026-10-02/PRODUCT_CARD_PAYLOADS.json'
    assert sha(payload_path)=='c57ba748ff31b920c517ec1e2ac131ce7c89059a84c0e7e288c755ebe5c979e6'
    payloads=json.loads(payload_path.read_bytes());validate(rows,payloads)
    frozen_inputs={'MATCHING_ACCEPTED.json':sha(matching),'EXPORT_INPUT_ROWS.json':sha(inputs),
                   'SELECTION_VERIFIED.json':sha(selection),'PRODUCT_CARD_PAYLOADS.json':sha(payload_path),
                   'CARDS.jsonl':sha(BASE/'RECONCILED/CARDS.jsonl')}
    OUT.mkdir(parents=True,exist_ok=True)
    prior=OUT/'ASSEMBLY_INPUTS.json'
    if prior.exists() and json.loads(prior.read_bytes())!=frozen_inputs:
        old=json.loads(prior.read_bytes())
        assert all(old[k]==frozen_inputs[k] for k in ('MATCHING_ACCEPTED.json','PRODUCT_CARD_PAYLOADS.json','CARDS.jsonl'))
        receipt=BASE/'SELECTION_RECONCILIATION_2026-10-06/PRE_RECONCILIATION_ASSEMBLY_INPUTS.json'
        if receipt.exists(): assert json.loads(receipt.read_bytes())==old
        else: receipt.write_bytes(prior.read_bytes())
    prior.write_text(json.dumps(frozen_inputs,indent=2),encoding='utf-8')
    (OUT/'accepted').mkdir(exist_ok=True)
    (OUT/'accepted/MATCHING_ACCEPTED.json').write_bytes(matching.read_bytes())
    (OUT/'PRODUCT_CARD_PAYLOADS.json').write_bytes(payload_path.read_bytes())
    root=ET.fromstring(xml_bytes(rows,payloads,frozen_inputs,selection_state=state))
    exclusions=json.loads((ROOT/'reports/KAMI_SOURCE_AUDIT_2026-10-05/ROBOTS_EXCLUSIONS.json').read_bytes())
    disclose=ET.SubElement(root,'ROBOTS_EXCLUDED',{'actionable':'false','product_count':'0','count':'2'})
    for item in exclusions['rows']:
        node=ET.SubElement(disclose,'ExcludedSourceURL',{'namespace':'kami','status':'ROBOTS_EXCLUDED'})
        node.text=json.dumps(item,ensure_ascii=False,sort_keys=True)
    # Supplier registry content is separate from canonical action buckets.
    # All own fields remain available even where identity is REVIEW.
    registry=ET.SubElement(root,'SupplierRegistry',{'namespace':'kami','actionable':'false','count':'5264'})
    cards=[json.loads(line) for line in (BASE/'RECONCILED/CARDS.jsonl').read_text().splitlines()]
    for item in cards:
        card=item['card'];node=ET.SubElement(registry,'SourceCard',{'external_id':card['external_id'],'classification':'REVIEW'})
        content={k:card[k] for k in ('name','brand','manufacturer','sku','canonical_url','price','old_price','currency',
                                   'price_raw','price_source','availability_raw','availability_normalized','quantity',
                                   'description_text','description_html','properties','media','options','categories')}
        content.update(full_model=card['raw_data'].get('full_model'),price_state=card['raw_data']['price_state'],
                       price_basis=card['raw_data']['price_basis'],property_evidence=card['raw_data'].get('property_evidence',[]),
                       capture=item['capture'],evidence_ref=item['evidence_ref'])
        node.text=json.dumps(content,ensure_ascii=False,sort_keys=True)
    ET.indent(root,space='  ');xml=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    assert xml==ET.tostring(root,encoding='utf-8',xml_declaration=True)
    ET.fromstring(xml)
    (OUT/'UNIVERSAL_SUPPLIER_KAMI_RC.xml').write_bytes(xml)
    data=tables(rows,payloads,selection_state=state)
    data['ROBOTS_EXCLUDED']=[{'source':'kami',**r} for r in exclusions['rows']]
    for name,records in data.items():
        keys=list(dict.fromkeys(k for record in records for k in record))
        with (OUT/(name.upper().replace(' ','_')+'.csv')).open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=keys);writer.writeheader();writer.writerows(records)
    workbook={'summary':summarize(rows),'selected_offers':len(state['selected']),'exclusions':2,'tables':data}
    (OUT/'WORKBOOK_DATA.json').write_text(json.dumps(workbook,ensure_ascii=False),encoding='utf-8')
    freeze={'contract':'universal-supplier-neutral/2.0','summary':summarize(rows),'selected_offers':len(state['selected']),
            'robots_excluded':2,'input_sha256':frozen_inputs,'HTTP':0,'SQL_writes':0,'matcher_rerun':False,
            'selection_rerun':False,'selection_source':'COMMITTED_CANONICAL_SQL',
            'selection_table_sha256':actual,'import_authorized':False,'workbook':'PENDING_AUTHORING_QA'}
    (OUT/'FREEZE_MANIFEST.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**freeze,'input_sha256':'see manifest','XML_bytes':len(xml),'csv_counts':{k:len(v) for k,v in data.items()}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
