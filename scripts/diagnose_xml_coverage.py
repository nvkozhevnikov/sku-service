"""Exact testDB SELECT diagnosis and local previews. No HTTP, matching or DB writes."""
from pathlib import Path
from collections import Counter,defaultdict
from dataclasses import asdict,is_dataclass
from datetime import datetime
import argparse,csv,json,sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def encoded(value):
    if is_dataclass(value):return asdict(value)
    return value.isoformat() if isinstance(value,datetime) else str(value)


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=encoded),encoding='utf-8')


def representative(rows,count=20):
    """Deterministic cross-source sample, then complete to the requested count."""
    groups=defaultdict(list)
    for row in sorted(rows,key=lambda r:(r['supplier_code'],str(r['external_id']))):groups[row['supplier_code']].append(row)
    selected=[]
    while len(selected)<count and any(groups.values()):
        for code in sorted(groups):
            if groups[code] and len(selected)<count:selected.append(groups[code].pop(0))
    return selected


def run(output,baseline=None):
    from universal_supplier.control_plane.runtime_config import runtime_config,guarded_connect
    from universal_supplier.preview_store import load
    from universal_supplier.dual_preview import build,publish
    from universal_supplier.commercial_freshness import assess
    from universal_supplier.control_plane.supplier_automation import settings_for
    from universal_supplier.yml_feed import PostgresFeedStore
    from universal_supplier.xml_export import generate_canonical_xml
    from universal_supplier.effective_identity import effective_identity_mapping_sql
    from psycopg.rows import dict_row

    output.mkdir(parents=True,exist_ok=True);cfg=runtime_config()
    s=load(cfg,connection_factory=guarded_connect)
    previous=json.loads(baseline.read_text(encoding='utf-8-sig')) if baseline else None
    data,m=build(s,baseline=previous)
    directory=publish(output/'previews',data,m)
    write_json(output/'AFTER_MANIFEST.json',m)
    write_json(output/'funnels_by_supplier.json',m['funnels_by_supplier'])
    write_json(output/'exclusions_by_supplier.json',m['coverage_by_supplier'])
    write_json(output/'records_all_reasons.json',m['record_trace'])
    fields=['kind','source','candidates','eligible','serialized_sources','excluded','primary_reason','count']
    with (output/'exclusions_by_supplier.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for kind,sources in m['coverage_by_supplier'].items():
            for code,counts in sources.items():
                for reason,n in counts['primary_reasons'].items():
                    writer.writerow({'kind':kind,'source':code,**{k:counts[k] for k in fields[2:6]},'primary_reason':reason,'count':n})
    allrows=s['records'];byid={r['source_product_id']:r for r in allrows}
    included=set(m['included_sources']['new_products'])
    groups={'confirmed':representative([r for r in allrows if r['identity_confirmed'] is True],20),
        'NEW_included':representative([byid[i] for i in included],20),
        'NEW_excluded':representative([r for r in allrows if r['match_status']=='NEW_CANDIDATE' and r['source_product_id'] not in included],20)}
    historical=[r for r in allrows if r['supplier_code']=='partner_st' and str(r['external_id'])=='297']
    groups['Stage5_PP800F']=historical
    ids=sorted({r['source_product_id'] for rows in groups.values() for r in rows})
    field_history={};sql_facts={}
    # Separate SELECT-only, exact guarded connection. No rollback simulation DML.
    with guarded_connect(cfg,autocommit=True,row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as c,c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        sql_facts['identity']=c.execute("SELECT current_database(),current_user,current_setting('data_directory'),system_identifier::text FROM pg_control_system()").fetchone()
        aggregate_query=f'''WITH facts AS (
          SELECT s.code,o.id AS offer_id,sp.catalog_product_id IS NOT NULL AS physical_link,
            {effective_identity_mapping_sql('sp.id','sp.catalog_product_id')} AS confirmed,
            pm.status AS match_status,pid.decision AS identity_decision,
            coalesce(pm.last_validated_at,pm.created_at)>=sp.last_success_at AS matching_validated,
            co.observed_at,co.price,co.currency,co.price_state,co.availability_normalized,
            (cap.http_status=200 AND cap.capture_fingerprint IS NOT NULL
              AND cap.source_product_id=sp.id AND cap.supplier_id=s.id
              AND co.source_product_id=sp.id AND co.supplier_id=s.id AND co.offer_id=o.id
              AND cap.observed_at=co.observed_at AND cap.diagnostics::text NOT ILIKE '%%SYNTHETIC%%'
              AND cap.diagnostics::text NOT ILIKE '%%NOT_HTTP%%'
              AND co.extraction_evidence::text NOT ILIKE '%%SYNTHETIC%%'
              AND co.extraction_evidence::text NOT ILIKE '%%NOT_HTTP%%') AS receipt_valid,
            coalesce((s.crawl_settings->'automation'->>'price_ttl')::int,21600) AS price_ttl,
            coalesce((s.crawl_settings->'automation'->>'availability_ttl')::int,10800) AS stock_ttl
          FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
          LEFT JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id
          LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
          LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
          LEFT JOIN LATERAL (SELECT * FROM offer_commercial_observations x
            WHERE x.source_product_id=sp.id AND x.offer_id=o.id
              AND coalesce(x.region_code,'')=coalesce(sp.raw_data->>'_commercial_projection_region','')
            ORDER BY x.observed_at DESC,x.id DESC LIMIT 1) co ON true
          LEFT JOIN supplier_http_captures cap ON cap.id=co.capture_id), fields AS (
            SELECT *,receipt_valid AND price>0 AND currency IS NOT NULL
              AND price_state IN ('numeric','numeric_public') AS numeric_price,
              receipt_valid AND availability_normalized IN
                ('in_stock','out_of_stock','incoming','preorder','backorder','discontinued') AS known_stock
            FROM facts), freshness AS (SELECT *,
              numeric_price AND observed_at<=transaction_timestamp()
                AND observed_at>=transaction_timestamp()-price_ttl*interval '1 second' AS price_fresh,
              known_stock AND observed_at<=transaction_timestamp()
                AND observed_at>=transaction_timestamp()-stock_ttl*interval '1 second' AS stock_fresh FROM fields)
          SELECT code,count(*) AS source_offer_rows,count(offer_id) AS offers,
            count(*) FILTER(WHERE physical_link) AS physical_links,
            count(*) FILTER(WHERE confirmed) AS confirmed,
            count(*) FILTER(WHERE confirmed AND code<>'beka_mak_tr') AS commercial_identity,
            count(*) FILTER(WHERE confirmed AND numeric_price) AS confirmed_price_available,
            count(*) FILTER(WHERE confirmed AND price_fresh) AS confirmed_price_fresh,
            count(*) FILTER(WHERE confirmed AND known_stock) AS confirmed_availability_known,
            count(*) FILTER(WHERE confirmed AND stock_fresh) AS confirmed_availability_fresh,
            count(*) FILTER(WHERE confirmed AND (price_fresh OR stock_fresh)) AS confirmed_any_fresh,
            count(*) FILTER(WHERE match_status='NEW_CANDIDATE') AS new_candidates,
            count(*) FILTER(WHERE match_status='NEW_CANDIDATE' AND matching_validated
              AND identity_decision='SAFE_NEW_PRODUCT_CANDIDATE') AS new_current_matching,
            count(*) FILTER(WHERE receipt_valid) AS receipt_valid
          FROM freshness GROUP BY code ORDER BY code'''
        sql_facts['aggregates']=c.execute(aggregate_query).fetchall()
        (output/'SQL_AGGREGATES_QUERY.sql').write_text(aggregate_query,encoding='utf-8')
        sql_facts['observation_ranges']=c.execute('''SELECT s.code,count(*) AS observations,
            min(o.observed_at) AS first_at,max(o.observed_at) AS last_at
            FROM offer_commercial_observations o JOIN suppliers s ON s.id=o.supplier_id GROUP BY s.code ORDER BY s.code''').fetchall()
        sql_facts['stored_selection']=c.execute('''SELECT selection_status,count(*),min(evaluated_at),max(evaluated_at)
            FROM catalog_offer_selection GROUP BY selection_status ORDER BY selection_status''').fetchall()
        sql_facts['crawl_success']=c.execute('''SELECT s.code,max(r.finished_at) FILTER(WHERE r.status='succeeded') AS last_successful_crawl,
            max(r.finished_at) AS last_finished,max(r.started_at) AS last_started
            FROM suppliers s LEFT JOIN crawl_runs r ON r.supplier_id=s.id GROUP BY s.code ORDER BY s.code''').fetchall()
        for field,condition in (('price',"o.price>0 AND o.currency IS NOT NULL AND o.price_state IN ('numeric','numeric_public')"),
                ('availability',"o.availability_normalized IN ('in_stock','out_of_stock','incoming','preorder','backorder','discontinued')")):
            rows=c.execute(f'''SELECT DISTINCT ON (o.source_product_id) o.source_product_id,o.id AS observation_id,
                o.offer_id,o.capture_id,o.price,o.currency,o.price_state,o.availability_normalized,
                o.observed_at,cap.capture_fingerprint,cap.requested_url,cap.evidence_ref,cap.http_status,
                o.extraction_evidence FROM offer_commercial_observations o
                JOIN supplier_http_captures cap ON cap.id=o.capture_id
                JOIN source_products sp ON sp.id=o.source_product_id
                WHERE o.source_product_id=ANY(%s) AND {condition}
                  AND coalesce(o.region_code,'')=coalesce(sp.raw_data->>'_commercial_projection_region','')
                ORDER BY o.source_product_id,o.observed_at DESC,o.id DESC''',(ids,)).fetchall()
            for row in rows:field_history.setdefault(row['source_product_id'],{})[field]=row
    # Historical Stage5 persisted selection remains independent. This artifact is
    # deliberately named historical; it is not promoted to current TTL eligibility.
    with guarded_connect(cfg,autocommit=True,options='-c default_transaction_read_only=on') as c,c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        legacy=PostgresFeedStore(c).load_snapshot()
    artifact=generate_canonical_xml(legacy)
    (output/'legacy_stage5_historical_compatibility.xml').write_bytes(artifact.data)
    write_json(output/'STAGE5_COMPATIBILITY.json',{'classification':'historical persisted selection; NOT fresh commercial data',
        'counts':artifact.manifest,'source_ids':[r.source_product_id for r in legacy.selected_rows],
        'PP800F_same_source_present':bool(historical),'PP800F_same_canonical_ID':bool(historical and historical[0]['confirmed_sterbrust_id']=='88585')})
    settings={r['code']:settings_for(r) for r in s['suppliers']}
    traces={kind:{r['source_product_id']:r for r in items} for kind,items in m['record_trace'].items()}
    samples={}
    for group,rows in groups.items():
        samples[group]=[]
        for row in rows:
            sample={k:row.get(k) for k in ('supplier_id','supplier_code','source_product_id','external_id','offer_id',
                'catalog_product_id','confirmed_sterbrust_id','identity_confirmed','name','brand_raw','own_model','product_kind',
                'source_last_success_at','last_selection_at','persisted_selection_status','persisted_selected_offer_id',
                'capture_id','capture_fingerprint','response_sha256','evidence_ref','capture_requested_url','http_status',
                'receipt_identity_valid','observation_id','observed_at','observed_price','observed_currency','observed_availability',
                'price_state','extraction_evidence','match_id','match_status','match_last_validated_at','audited_at','decision',
                'matching_current_for_capture','review_lifecycle','review_automatic_decision','latest_manual_decision',
                'blocking_review','blocking_quarantine')}
            sample['field_freshness']=assess(row,settings[row['supplier_code']],s['as_of'])
            sample['last_known_numeric_price_or_stock_diagnostic_only']=field_history.get(row['source_product_id'],{})
            sample['updates']=traces['updates'][row['source_product_id']]
            sample['new_products']=traces['new_products'][row['source_product_id']]
            samples[group].append(sample)
    write_json(output/'record_traces_20_plus_20.json',samples)
    write_json(output/'SQL_FACTS.json',sql_facts)
    # Explicit targets for a FUTURE separately approved refresh; not enqueue instructions.
    refresh=[{k:r.get(k) for k in ('supplier_id','supplier_code','source_product_id','external_id','source_url','offer_id','confirmed_sterbrust_id')}
        for r in allrows if r['identity_confirmed'] is True and not r.get('blocking_quarantine') and r['supplier_code']!='beka_mak_tr']
    write_json(output/'REFRESH_TARGETS_NOT_APPROVED.json',{'execution':'NOT_APPROVED; no HTTP/enqueue performed',
        'purpose':'obtain current per-field commercial observations; preserve exact canonical IDs',
        'unique_source_targets':len(refresh),'targets':refresh})
    result={'as_of':s['as_of'],'classification':'LIVE-VERIFIED read-only SELECT/local XML; NOT new LIVE collection',
        'counts':m['counts'],'checksums':m['checksums'],'version':m['version'],'quality_gate':m['quality_gate'],
        'new_readiness_counts':m['new_readiness_counts'],'directory':str(directory),'record_count':len(allrows),
        'confirmed_links':sum(r['identity_confirmed'] is True for r in allrows),
        'sample_sizes':{k:len(v) for k,v in samples.items()},'HTTP':0,'SQL_writes':0,'matching':0,'enqueue':0,'import_allowed':False}
    write_json(output/'RESULT.json',result)
    print(json.dumps(result,default=encoded,ensure_ascii=True))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--baseline',type=Path);args=parser.parse_args();run(args.output,args.baseline)


if __name__=='__main__':main()
