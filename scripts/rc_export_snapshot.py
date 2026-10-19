"""Human exports from effective persisted authority; no matcher/apply/import.

Zero operation buckets explain the missing approval/comparable-price evidence.
"""
import csv,json,hashlib,sys
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime,timezone
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rc_prepare_database import OUT,TARGET,connect
from rc_runtime import FrozenStore,config
from universal_supplier.control_plane.store import ProductFilters
from psycopg.rows import dict_row

DELIVERY=ROOT/'release/DELIVERY_2026-10-19_RC1'
FIELDS=['sterbrust_product_id','supplier_namespace','source_external_id','canonical_source_url','brand','model_raw','model_strict','manufacturer_article','supplier_article','category','characteristics','image_urls','description','price_rub','price_state','availability','observed_at','identity_status','selection_source','evidence_ref','warnings','persisted_sterbrust_product_id']

def csv_file(name,rows,fields=None):
    fields=fields or FIELDS
    with (DELIVERY/'exports'/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for row in rows:
            safe={k:json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list,tuple)) else str(v) if v is not None else '' for k,v in row.items()}
            # Spreadsheet interpreters must treat remote text as literal content.
            safe={k:"'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for k,v in safe.items()}
            w.writerow(safe)

def main():
    (DELIVERY/'exports').mkdir(parents=True,exist_ok='--refresh-own-draft' in sys.argv)
    store=FrozenStore(config())
    rows=list(store.iter_export_rows(ProductFilters()))
    from sterbrust_matching.normalization import normalize_model_strict
    props=defaultdict(list);images=defaultdict(list)
    for offset in range(0,len(rows),500):
        details=store.export_details([r['source_product_id'] for r in rows[offset:offset+500]])
        for p in details['properties']:props[p['source_product_id']].append({k:p[k] for k in ('raw_name','raw_value','raw_unit')})
        for p in details['images']:images[p['source_product_id']].append(p['source_url'])
    with connect(TARGET) as c,c.cursor(row_factory=dict_row) as cur:
        cur.execute('SET default_transaction_read_only=on')
        source_meta={r['id']:r for r in cur.execute('SELECT id,raw_data FROM source_products').fetchall()}
        commercial={r['source_product_id']:r for r in cur.execute('SELECT DISTINCT ON(source_product_id) source_product_id,price_state,observed_at,extraction_evidence FROM offer_commercial_observations ORDER BY source_product_id,observed_at DESC,id DESC').fetchall()}
        selected={r['source_product_id'] for r in cur.execute('SELECT o.source_product_id FROM catalog_offer_selection s JOIN offers o ON o.id=s.selected_offer_id').fetchall()}
        news=cur.execute("SELECT s.code supplier_namespace,p.canonical_url,p.title,p.page_type,p.first_seen_at,p.last_seen_at,v.fetched_at,v.raw_capture_ref FROM competitor.competitor_pages p JOIN competitor.competitor_sources s ON s.id=p.source_id JOIN competitor.competitor_page_versions v ON v.page_id=p.id AND v.content_hash=p.current_content_hash WHERE p.page_type IN ('NEWS','BLOG','ARTICLE','EVENT','CONTENT','NEW_PRODUCT_ANNOUNCEMENT') ORDER BY s.code,p.id").fetchall()
    replay=json.loads((OUT/'MATCHING_DRY_RUN/after.json').read_text(encoding='utf-8'))
    by_key={(r['supplier_code'],r['external_id']):r for r in replay['rows'] if r['pass_mode']=='with_existing_links'}
    existing=[];review=[];trace=[];revalidation_review=0
    for r in rows:
        sid=r['source_product_id'];raw=source_meta[sid]['raw_data'] or {};co=commercial.get(sid,{})
        identity=raw.get('source_identity') or {};capture=raw.get('commercial_capture') or {};content=raw.get('source_content') or {}
        model=identity.get('raw_model') or r.get('own_model') or ''
        pstate=co.get('price_state') or ('numeric_public' if r.get('price') is not None and r['price']>0 else 'missing')
        price=r.get('price') if pstate=='numeric_public' and r.get('currency')=='RUB' else None
        record={'sterbrust_product_id':r.get('confirmed_sterbrust_id'),'supplier_namespace':r['supplier_code'],'source_external_id':r['external_id'],'canonical_source_url':r['canonical_url'] or r['source_url'],'brand':r['brand_raw'],'model_raw':model,'model_strict':normalize_model_strict(model,r['brand_raw']),'manufacturer_article':identity.get('manufacturer_article') or '','supplier_article':r['sku'],'category':r['category'] or content.get('source_category') or '', 'characteristics':props[sid] or content.get('technical_properties') or [],'image_urls':images[sid] or content.get('source_images') or [],'description':r['description_text'],'price_rub':price,'price_state':pstate,'availability':r['availability_normalized'],'observed_at':co.get('observed_at') or r['last_seen_at'],'identity_status':'EXISTING_CONFIRMED_PERSISTED' if r.get('confirmed_sterbrust_id') else 'REVIEW','selection_source':'PERSISTED_SELECTED_OFFER' if sid in selected else 'SOURCE_OFFER_NOT_SELECTED','evidence_ref':capture.get('evidence_ref') or (co.get('extraction_evidence') or {}).get('evidence_ref') or '', 'warnings':['HISTORICAL_SNAPSHOT','MATCHING_RELEASE_GATE_NOT_YET_ACCEPTED']}
        record['persisted_sterbrust_product_id']=record['sterbrust_product_id']
        proposed=by_key[(r['supplier_code'],r['external_id'])]
        if record['sterbrust_product_id'] and (proposed['auto_accepted']!='TRUE' or str(proposed['sterbrust_product_id'])!=str(record['sterbrust_product_id'])):
            revalidation_review+=1
            record['sterbrust_product_id']=None
            record['identity_status']='REVIEW_PERSISTED_LINK_REVALIDATION_REQUIRED'
            record['warnings']+=['PERSISTED_LINK_UNCHANGED',proposed['match_method'],proposed['conflicts']]
        if r['supplier_code']=='intervesp':record['warnings'].append('NEWER_55452_SNAPSHOT_BLOCKED_MISSING_VERIFIED_SOURCE_DATA')
        (existing if record['sterbrust_product_id'] else review).append(record)
        trace.append({**record,'source_product_id':sid,'source_active':r['active']})
    csv_file('EXISTING_CONFIRMED.csv',existing);csv_file('REVIEW_CONFLICT.csv',review)
    csv_file('SOURCE_TRACE.csv',trace,FIELDS+['source_product_id','source_active'])
    csv_file('COMPETITOR_NEWS_SNAPSHOT.csv',news,list(news[0]) if news else ['supplier_namespace','canonical_url','title','page_type','first_seen_at','last_seen_at','fetched_at','raw_capture_ref'])
    reasons={'NEW_PRODUCTS':'NO_PERSISTED_FULL_NEW_APPROVAL_IN_UNIFIED_TARGET','PRICE_UPDATES':'NO_VERIFIED_COMPARABLE_STERBRUST_PRICE_DELTA'}
    for name,reason in reasons.items():
        root=ET.Element('operations',{'contract':'universal-supplier-neutral/2.0','operation':name,'records':'0','reason':reason,'import_authorized':'false'})
        (DELIVERY/'exports'/(name+'.xml')).write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
        ET.parse(DELIVERY/'exports'/(name+'.xml'))
    manifest={'status':'PARTIAL_RC_NOT_IMPORT_AUTHORIZED','source_rows':len(rows),'existing_persisted_replay_consistent':len(existing),'persisted_links_moved_to_review_export_only':revalidation_review,'review':len(review),'competitor_news':len(news),'operation_buckets':{k:{'records':0,'reason':v} for k,v in reasons.items()},'snapshot_cutoff':'2026-10-19','source_dates_preserved':True,'known_omissions':['vekprom: BLOCKED_MISSING_VERIFIED_SOURCE_DATA','intervesp newer source snapshot 55452: BLOCKED_MISSING_VERIFIED_SOURCE_DATA'],'matching_gate':'NOT_VERIFIED','inactive_history':'All inactive records remain in database; human exports active only (12120).','files':[]}
    for p in sorted((DELIVERY/'exports').iterdir()):manifest['files'].append({'file':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    (DELIVERY/'EXPORT_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (OUT/'XLSX_OPERATION_INPUT.json').write_text(json.dumps(reasons,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'},indent=2))
if __name__=='__main__':main()
