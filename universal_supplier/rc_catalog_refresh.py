"""Explicit isolated RC refresh writer. No crawling/matching/selection hooks.

Preserves enrichment, canonical links, primary keys and activation states.
Observations are append-only; a replay returns before any UPDATE/INSERT.
"""
import json,hashlib
from psycopg.types.json import Jsonb

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()

def rc_availability(value):
    normalized='preorder' if value=='on_request' else value
    if normalized not in {'unknown','in_stock','out_of_stock','preorder','incoming','backorder','discontinued'}:
        raise ValueError('Unknown availability state')
    return normalized

def apply_one(cursor,row,*,commit,observed_at,evidence_ref):
    supplier=row['supplier_code']
    if supplier not in {'partner_st','optimum'}:raise ValueError('Refresh namespace forbidden')
    if row.get('identity_conflict'):raise ValueError('Unresolved identity conflict')
    external=str(row['source_identity'])
    fp=fingerprint({'scope':'verified_saved_catalog_refresh','commit':commit,'row':row})
    cursor.execute('SELECT id FROM suppliers WHERE code=%s FOR UPDATE',(supplier,));sid=cursor.fetchone()[0]
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'refresh:{sid}:{external}',))
    cursor.execute('SELECT id,raw_data,name,sku,source_url,canonical_url,active,catalog_product_id FROM source_products WHERE supplier_id=%s AND external_id=%s FOR UPDATE',(sid,external))
    previous=cursor.fetchone()
    if previous:
        pid=previous[0]
        cursor.execute('SELECT id FROM supplier_http_captures WHERE source_product_id=%s AND capture_fingerprint=%s',(pid,fp))
        existing=cursor.fetchone()
        if existing:return {'external_id':external,'source_product_id':pid,'exact_noop':True,'capture_id':existing[0]}
        raw=dict(previous[1] or {})
    else:raw={}
    raw['_catalog_refresh']={'commit':commit,'evidence_ref':evidence_ref,'current':row,
                            'provisional':supplier=='optimum','no_hidden_identity_override':True}
    # Preserve all historical detailed content; current listing is a projection,
    # not a claim that absent listing characteristics were deleted from source.
    if previous:
        cursor.execute('UPDATE source_products SET name=%s,sku=%s,source_url=%s,canonical_url=%s,raw_data=%s,last_seen_at=%s,last_success_at=%s,updated_at=now() WHERE id=%s',
                       (row['visible_name'],row['visible_article'] or previous[3],row['product_url'],row['product_url'],Jsonb(raw),observed_at,observed_at,pid))
    else:
        cursor.execute('INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,sku,name,source_url,canonical_url,raw_data,first_seen_at,last_seen_at,last_success_at,last_changed_at,active,missed_crawls) VALUES(%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,0) RETURNING id',
                       (sid,external,row['visible_article'],row['visible_name'],row['product_url'],row['product_url'],Jsonb(raw),observed_at,observed_at,observed_at,observed_at))
        pid=cursor.fetchone()[0]
    price=row['normalized_price'];state='numeric_public' if row['price_state']=='numeric' else row['price_state']
    availability=rc_availability(row['availability'])
    offer_raw={'_catalog_refresh':raw['_catalog_refresh']}
    cursor.execute("SELECT id,raw_data,active FROM offers WHERE source_product_id=%s AND offer_kind='default' FOR UPDATE",(pid,))
    offer=cursor.fetchone()
    if offer:
        oid=offer[0];merged=dict(offer[1] or {});merged.update(offer_raw)
        cursor.execute('UPDATE offers SET title=%s,price=%s,currency=%s,availability_raw=%s,availability_normalized=%s,price_raw=%s,price_source=%s,raw_data=%s,last_seen_at=%s,last_success_at=%s,updated_at=now() WHERE id=%s',
                       (row['visible_name'],price,'RUB' if price is not None else None,row['availability_raw'],availability,row.get('price_amount') or row['price_state'],'verified_saved_visible_catalog',Jsonb(merged),observed_at,observed_at,oid))
    else:
        cursor.execute("INSERT INTO offers(supplier_id,source_product_id,external_offer_id,external_id_is_stable,offer_kind,sku,title,price,currency,availability_raw,availability_normalized,price_type,price_raw,price_source,raw_data,first_seen_at,last_seen_at,last_success_at,last_changed_at,active,missed_crawls) VALUES(%s,%s,%s,true,'default',%s,%s,%s,%s,%s,%s,'unknown',%s,%s,%s,%s,%s,%s,%s,false,0) RETURNING id",
                       (sid,pid,external,row['visible_article'],row['visible_name'],price,'RUB' if price is not None else None,row['availability_raw'],availability,row.get('price_amount') or row['price_state'],'verified_saved_visible_catalog',Jsonb(offer_raw),observed_at,observed_at,observed_at,observed_at))
        oid=cursor.fetchone()[0]
    digest=fingerprint(row)
    diagnostics={'capture_scope':'verified_saved_catalog_refresh','response_hash_basis':'normalized_catalog_record_not_http_response',
                 'record_time_origin':'evidence_commit_timestamp_not_claimed_http_clock','commit':commit}
    cursor.execute('INSERT INTO supplier_http_captures(supplier_id,source_product_id,requested_url,final_url,http_status,content_type,observed_at,capture_fingerprint,response_sha256,evidence_sha256,evidence_ref,redirect_urls,region_code,diagnostics) VALUES(%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                   (sid,pid,row['product_url'],row['product_url'],'application/x-ndjson',observed_at,fp,digest,digest,evidence_ref,Jsonb([]),'',Jsonb(diagnostics)))
    cid=cursor.fetchone()[0]
    cursor.execute('INSERT INTO offer_commercial_observations(supplier_id,source_product_id,offer_id,capture_id,observed_at,extraction_fingerprint,price_state,price,currency,availability_raw,availability_normalized,price_type,price_raw,price_source,extraction_evidence,unusable_price_reasons,region_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                   (sid,pid,oid,cid,observed_at,fp,state,price,'RUB' if price is not None else None,row['availability_raw'],availability,'unknown',row.get('price_amount') or row['price_state'],'verified_saved_visible_catalog',Jsonb({'visible_primary':True,'secondary_hidden_metadata':row['hidden_price_metadata'],'evidence_ref':evidence_ref,'commit':commit}),Jsonb([] if price is not None else [state]),''))
    observation=cursor.fetchone()[0]
    return {'external_id':external,'source_product_id':pid,'offer_id':oid,'capture_id':cid,'observation_id':observation,
            'new_source_product':previous is None,'exact_noop':False}
