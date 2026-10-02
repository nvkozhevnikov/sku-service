"""Reuse saved detail characteristics in proposal evaluation only; no ingestion."""
from pathlib import Path
from copy import deepcopy
from collections import Counter
import sys,json,hashlib
from lxml import html
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.close_unresolved_saved_evidence import OUT,PRE,load
from universal_supplier.beka_canonical_offline import canonical_view,model_key,SavedCandidateIndex
from universal_supplier.offline_canonical_closure import strict_saved_resolution,own_model_columns
from universal_supplier.offline_review_resolution import source_title_model,learn_cnc_mappings
from universal_supplier.characteristic_evidence import build_evidence,equipment_scope,unit_from_label
from scripts.build_characteristic_evidence import section_evidence
from scripts.match_fresh_refresh_consolidated import summary,NAMESPACES
from sterbrust_matching.normalization import normalize_text
DEST=OUT/'SOURCE_CAPTURE_REUSE'
GROK=ROOT.parent/'sku-service-fresh-catalog-refresh-rc1'
def save(name,v):(DEST/name).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def main():
    assert not (DEST/'MATCHING_AFTER.json').exists(),'Already evaluated new saved properties; resume'
    DEST.mkdir(exist_ok=True)
    accepted=load(OUT/'MATCHING_AFTER.json');rows=accepted['rows'];views={};captureproof=[]
    proof=load(ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/EVIDENCE_VERIFIED.json')
    for supplier in ('partner_st','optimum'):
        base=GROK/'catalog_refresh/evidence'/supplier
        indexbytes=(base/'CAPTURE_INDEX.jsonl').read_bytes()
        assert hashlib.sha256(indexbytes).hexdigest()==proof['provenance_sha256'][f'catalog_refresh/evidence/{supplier}/CAPTURE_INDEX.jsonl']
        byurl={r['canonical_url']:r for r in [json.loads(line) for line in indexbytes.splitlines()]}
        for row in rows:
            if row['source']!=supplier or row['classification']=='CONFLICT':continue
            cap=byurl.get(row['source_url'])
            if not cap:continue
            path=(base/'captures'/cap['file']).resolve()
            assert path.is_relative_to((base/'captures').resolve())
            body=path.read_bytes();assert hashlib.sha256(body).hexdigest()==cap['sha256'] and len(body)==cap['bytes']
            doc=html.fromstring(body);h1=doc.xpath('//h1')
            if not h1:continue
            visible=' '.join(' '.join(h1[0].itertext()).split())
            observed=source_title_model({'name':visible,'brand':row.get('brand','')})
            if not model_key(row) or model_key({**row,'model':observed})!=model_key(row):continue
            props=[]
            if supplier=='partner_st':
                candidates=doc.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' ty-product-feature ')]")
                for node in candidates:
                    labels=node.xpath(".//*[contains(@class,'ty-product-feature__label')]")
                    values=node.xpath(".//*[contains(@class,'ty-product-feature__value')]")
                    if labels and values:
                        label=' '.join(' '.join(labels[0].itertext()).split()).rstrip(':')
                        value=' '.join(' '.join(values[0].itertext()).split())
                        if label and value:props.append({'name':label,'value':value,'unit':unit_from_label(label)})
            else:
                root=doc.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' catalog-element ')]")
                if not root:continue
                nodes=root[0].xpath(".//*[@data-id='properties']//*[contains(@class,'catalog-element-properties-detail-item')]")
                for node in nodes:
                    labels=node.xpath(".//*[contains(@class,'catalog-element-properties-detail-item-name')]")
                    values=node.xpath(".//*[contains(@class,'catalog-element-properties-detail-item-value')]")
                    if labels and values:
                        label=' '.join(' '.join(labels[0].itertext()).split()).rstrip(':')
                        value=' '.join(' '.join(values[0].itertext()).split())
                        if label and value:props.append({'name':label,'value':value,'unit':unit_from_label(label)})
            if not props:continue
            key=row['source'],str(row['external_id']);view=deepcopy(row)
            old=view.get('observed_properties') or []
            seen={(p['name'],str(p['value']),p.get('unit','')) for p in old}
            new=[p for p in props if (p['name'],str(p['value']),p.get('unit','')) not in seen]
            if not new:continue
            # Append, never replace inconvenient source values or conflicts.
            view['observed_properties']=old+new
            view['saved_source_characteristic_provenance']={'sha256':cap['sha256'],'path':str(path),'visible_h1':visible}
            views[key]=view;captureproof.append({'source':key[0],'external_id':key[1],'new_properties':len(new),
                'sha256':cap['sha256'],'path':str(path),'classification':row['classification']})
    save('CAPTURE_REUSE_PROOF.json',captureproof)
    wanted={str(row.get('sterbrust_product_id') or row.get('proposed_sterbrust_id')) for row in rows
            if row['classification']=='EXISTING_CONFIRMED' or (row['source'],str(row['external_id'])) in views}
    # Canonical identity exact indices were already evaluated. Reuse the saved
    # exact retrieval IDs; only source typed properties are new in this step.
    audits=load(OUT/'STRICT_EVALUATIONS.json');by_audit={(a['source'],a['external_id']):a for a in audits}
    for key in views:
        if key in by_audit:wanted.update(by_audit[key]['retrieval']['exact_full_model_ids'])
    targets={};h=hashlib.sha256()
    for line in Path(proof['fresh_raw_external_path']).open('rb'):
        h.update(line);record=json.loads(line);pid=str(record['sterbrust_product_id'])
        if pid in wanted:
            view=canonical_view(record);view['observed_properties']+=own_model_columns(record)
            view.update(product_url=record.get('product_url'),category_ids=record.get('category_ids') or [record.get('category_id')])
            targets[pid]=view
    assert h.hexdigest()==proof['fresh_raw_sha256']
    sections={str(r['id']):r for r in load(ROOT/'reports/rest/STERBRUST_SECTIONS_RAW.json')};pairs=[]
    for row in rows:
        if row['classification']!='EXISTING_CONFIRMED':continue
        key=row['source'],str(row['external_id']);view=views.get(key,row);t=targets[row['sterbrust_product_id']]
        pairs.append({**view,'equipment_type':equipment_scope(view['name'],view.get('source_category','')),
            'source_properties':view.get('observed_properties') or [],'sterbrust_properties':t['observed_properties'],
            'sections':section_evidence(t['category_ids'],sections)})
    assert len(pairs)==454
    learned=build_evidence(pairs);cnc=learn_cnc_mappings(pairs)
    save('CONFIRMED_ONLY_EVIDENCE.json',learned)
    result=[];new=[];output=deepcopy(rows)
    for row in output:
        key=row['source'],str(row['external_id'])
        if key not in views or row['classification']!='REVIEW':continue
        oldaudit=by_audit[key];exact=oldaudit['retrieval']['exact_full_model_ids']
        candidates=[targets[p] for p in exact if p in targets]
        for target in candidates:target['equivalent_identity_ids']=exact # preserve global duplicate evidence
        audit=strict_saved_resolution(views[key],candidates,cnc)
        result.append({'source':key[0],'external_id':key[1],**audit,'source_properties':views[key]['observed_properties'],
            'source_evidence':views[key]['saved_source_characteristic_provenance']})
        if audit['confirmed']:
            pid=audit['confirmed']['canonical_id'];row.update(classification='EXISTING_CONFIRMED',
                sterbrust_product_id=pid,sterbrust_name=targets[pid]['name'],proposed_sterbrust_id='',
                full_model_confirmed=True,offline_expanded_source_proof=result[-1])
            new.append({'source':key[0],'external_id':key[1],'canonical_id':pid,'previous_route':oldaudit['previous_route']})
    summaryvalue={'overall':summary(output),'new_existing':len(new),'saved_pages_reused':len(captureproof),
        'existing_training_pages_reused':sum(p['classification']=='EXISTING_CONFIRMED' for p in captureproof),
        'new_typed_property_rows_evaluated':len(result),'closed_by_previous_route':dict(Counter(r['previous_route'] for r in new)),
        'http':0,'sql':0,'global_matcher':False,'beka_audits_repeated':False}
    save('EVALUATIONS.json',result);save('PROMOTIONS.json',new)
    save('MATCHING_AFTER.json',{'rows':output,'summary':summaryvalue});save('SUMMARY.json',summaryvalue)
    print(json.dumps(summaryvalue),flush=True)
if __name__=='__main__':main()
