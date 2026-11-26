"""One repeatable read snapshot for both previews; no DB mutation or HTTP."""
from datetime import datetime,timezone
from universal_supplier.partner_pilot import connect_postgres
from universal_supplier.effective_identity import effective_identity_mapping_sql
from .offer_selection import PostgresOfferSelectionStore


def matching_validation_is_current(row):
    """Validation time proves rechecking; no-op identity materialization has no new audit date."""
    from .commercial_freshness import timestamp
    expected={'NEW_CANDIDATE':'SAFE_NEW_PRODUCT_CANDIDATE','EXACT_MATCH':'EXACT_EXISTING',
        'HIGH_CONFIDENCE_MATCH':'HIGH_CONFIDENCE_EXISTING','REVIEW':'REVIEW_EXISTING','CONFLICT':'CONFLICT'}
    validated=timestamp(row.get('match_last_validated_at')) or timestamp(row.get('match_created_at'))
    observed=timestamp(row.get('source_last_success_at'))
    return bool(validated and observed and validated>=observed
        and row.get('match_status') in expected and expected[row['match_status']]==row.get('decision'))


def mark_quarantine(records,runs):
    """A newer rejected source observation must not revive an older offer in a feed."""
    from .commercial_freshness import timestamp
    blocked={}
    for run in runs:
        for receipt in (run['runtime'] or {}).get('quarantined') or []:
            at=timestamp((receipt.get('original_capture_metadata') or {}).get('observed_at')) or timestamp((receipt.get('http_evidence') or {}).get('observed_at')) or timestamp(run['started_at'])
            for kind,key in (('id',receipt.get('external_id')),('url',receipt.get('url'))):
                if key:
                    token=(run['code'],kind,str(key))
                    if token not in blocked:blocked[token]=at
                    elif blocked[token] is None or at is None:blocked[token]=None
                    else:blocked[token]=max(blocked[token],at)
    for row in records:
        tokens=[(row['supplier_code'],'id',str(row['external_id']))]+[
            (row['supplier_code'],'url',u) for u in (row.get('source_url'),row.get('canonical_url'),row.get('capture_requested_url')) if u]
        success=timestamp(row.get('source_last_success_at'))
        row['blocking_quarantine']=any(t in blocked and (blocked[t] is None or success is None or blocked[t]>=success) for t in tokens)


def load(config,*,connection_factory=None):
    import psycopg
    from psycopg.rows import dict_row,tuple_row
    with (connection_factory or connect_postgres)(config,autocommit=True,row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as c,c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        as_of=c.execute('SELECT transaction_timestamp() AS at').fetchone()['at']
        suppliers=c.execute('SELECT id,code,name,enabled,base_url,rate_settings,crawl_settings FROM suppliers ORDER BY code').fetchall()
        records=c.execute(f'''SELECT sp.id AS source_product_id,sp.supplier_id,s.code AS supplier_code,
            sp.catalog_product_id,sp.external_id,sp.external_id_is_stable,sp.sku,sp.name,sp.brand_raw,
            sp.manufacturer_raw,sp.description_text,sp.description_html,sp.source_url,sp.canonical_url,
            sp.last_success_at AS source_last_success_at,sp.last_success_at,sp.last_changed_at,sp.active,
            coalesce(nullif(sp.raw_data->'source_content'->>'source_category',''),cat.category_path) AS category,
            o.id AS offer_id,o.price,o.currency,o.quantity,o.availability_normalized,o.availability_raw,
            o.price_raw,o.price_source,o.price_type,
            obs.id AS observation_id,obs.capture_id,obs.observed_at,obs.price_state,obs.price AS observed_price,
            obs.currency AS observed_currency,obs.availability_normalized AS observed_availability,
            obs.availability_raw AS observed_availability_raw,obs.unusable_price_reasons,
            obs.price_source AS observed_price_source,obs.price_type AS observed_price_type,
            cap.capture_fingerprint,cap.http_status,cap.requested_url AS capture_requested_url,
            cap.response_sha256,cap.evidence_ref,cap.diagnostics AS capture_diagnostics,obs.extraction_evidence,
            (cap.source_product_id=sp.id AND cap.supplier_id=s.id AND obs.supplier_id=s.id
                AND obs.source_product_id=sp.id AND obs.offer_id=o.id
                AND cap.observed_at=obs.observed_at) AS receipt_identity_valid,
            pid.decision,pid.own_model,pid.reference_model,pid.product_kind,pid.conflicts,
            pid.identity_characteristics,pid.decision_reason,pid.rule_version,pid.audited_at,
            pm.id AS match_id,pm.status AS match_status,pm.conflicts AS match_conflicts,
            pm.last_validated_at AS match_last_validated_at,pm.created_at AS match_created_at,
            {effective_identity_mapping_sql('sp.id','sp.catalog_product_id')} AS identity_confirmed,
            (SELECT min(sb.sterbrust_product_id) FROM sterbrust_products sb
                WHERE sb.catalog_product_id=sp.catalog_product_id) AS confirmed_sterbrust_id,
            (SELECT count(*) FROM sterbrust_products sb WHERE sb.catalog_product_id=sp.catalog_product_id) AS canonical_id_count,
            EXISTS(SELECT 1 FROM review_cases rc WHERE rc.source_product_id=sp.id
                AND rc.lifecycle_status IN ('OPEN','ASSIGNED','REOPENED_SOURCE_CHANGED')) AS blocking_review,
            rc.lifecycle_status AS review_lifecycle,rc.automatic_decision AS review_automatic_decision,
            rc.source_identity_fingerprint AS review_identity_fingerprint,
            rc.source_identity_snapshot AS review_identity_snapshot,
            (SELECT rd.decision_class FROM review_decisions rd WHERE rd.source_product_id=sp.id
                AND rd.decision_class<>'POSTPONED' ORDER BY rd.created_at DESC,rd.id DESC LIMIT 1)
                AS latest_manual_decision,
            sel.selected_offer_id AS persisted_selected_offer_id,sel.selection_status AS persisted_selection_status,
            sel.evaluated_at AS last_selection_at
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            LEFT JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id
            LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
            LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            LEFT JOIN review_cases rc ON rc.source_product_id=sp.id
            LEFT JOIN catalog_offer_selection sel ON sel.catalog_product_id=sp.catalog_product_id
            LEFT JOIN LATERAL (SELECT string_agg(node.raw_name,' / ' ORDER BY node.position) AS category_path
                FROM product_category_paths path JOIN product_category_path_nodes node
                  ON node.path_id=path.id AND node.supplier_id=path.supplier_id
                WHERE path.source_product_id=sp.id AND path.supplier_id=sp.supplier_id AND path.is_current) cat ON true
            LEFT JOIN LATERAL (SELECT * FROM offer_commercial_observations co
                WHERE co.source_product_id=sp.id AND co.offer_id=o.id
                  AND COALESCE(co.region_code,'')=COALESCE(sp.raw_data->>'_commercial_projection_region','')
                ORDER BY co.observed_at DESC,co.id DESC LIMIT 1) obs ON true
            LEFT JOIN supplier_http_captures cap ON cap.id=obs.capture_id
            ORDER BY s.code,sp.id,o.id''').fetchall()
        registry=c.execute('SELECT sterbrust_product_id,catalog_product_id,brand_raw,model_raw FROM sterbrust_products ORDER BY id').fetchall()
        quarantines=c.execute('''SELECT DISTINCT ON (s.code,coalesce(j.settings_snapshot->'automation'->>'phase',
                j.settings_snapshot->'source_batch'->>'phase',j.settings_snapshot->'source_input'->>'mode'))
                s.code,r.started_at,r.metadata->'source_runtime' AS runtime
                FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id JOIN crawl_runs r ON r.id=j.crawl_run_id
                WHERE r.metadata ? 'source_runtime'
                ORDER BY s.code,coalesce(j.settings_snapshot->'automation'->>'phase',
                j.settings_snapshot->'source_batch'->>'phase',j.settings_snapshot->'source_input'->>'mode'),j.id DESC''').fetchall()
        mark_quarantine(records,quarantines)
        for row in records:
            if row['canonical_id_count']!=1:row['confirmed_sterbrust_id']=None
            row['matching_current_for_capture']=matching_validation_is_current(row)
        class Tuples:
            def cursor(self):return c.cursor(row_factory=tuple_row)
        candidates=PostgresOfferSelectionStore(Tuples()).load()
        details={}
        for row in c.execute('SELECT source_product_id,raw_name,raw_value,raw_unit,normalized_name,normalized_value,normalized_unit FROM product_properties ORDER BY source_product_id,position,id').fetchall():
            details.setdefault(row['source_product_id'],{}).setdefault('properties',[]).append(row)
        for row in c.execute('SELECT source_product_id,media_type,source_url,is_primary,document_type FROM product_media ORDER BY source_product_id,position,id').fetchall():
            key='images' if row['media_type']=='image' else 'documents' if row['media_type']=='document' else None
            if key:details.setdefault(row['source_product_id'],{}).setdefault(key,[]).append(row)
        coverage=c.execute('''SELECT s.code,j.id,j.status,j.error_message,j.finished_at FROM suppliers s
            LEFT JOIN LATERAL (SELECT id,status,error_message,finished_at FROM crawl_jobs
                WHERE supplier_id=s.id ORDER BY id DESC LIMIT 1) j ON true ORDER BY s.code''').fetchall()
        inventory={key:c.execute('SELECT count(*) n FROM '+table).fetchone()['n'] for key,table in
            [('source_products','source_products'),('offers','offers'),('commercial_observations','offer_commercial_observations'),('http_captures','supplier_http_captures')]}
        inventory['suppliers']=[[s['id'],s['code']] for s in suppliers]
        # Counts alone cannot detect deleting one identity and inserting another.
        for key,query in (
            ('source_identities','SELECT id,supplier_id,external_id FROM source_products ORDER BY id'),
            ('offer_identities','SELECT id,source_product_id,supplier_id FROM offers ORDER BY id'),
            ('observation_identities','SELECT id,offer_id,capture_id FROM offer_commercial_observations ORDER BY id'),
            ('capture_identities','SELECT id FROM supplier_http_captures ORDER BY id')):
            inventory[key]=[list(r.values()) for r in c.execute(query).fetchall()]
        inventory['canonical_links']=[list(r.values()) for r in c.execute('''SELECT sp.id,sp.catalog_product_id,sb.sterbrust_product_id
            FROM source_products sp JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
            WHERE sp.catalog_product_id IS NOT NULL ORDER BY sp.id,sb.sterbrust_product_id''').fetchall()]
        return {'data_inventory':inventory,'complete':True,'as_of':as_of,'suppliers':suppliers,'records':records,
                'registry':registry,'offer_candidates':candidates,'details':details,
                'coverage':{'sources':coverage,'external_catalog_completeness':'NOT_PROVEN'}}
