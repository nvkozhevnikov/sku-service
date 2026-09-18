BEGIN;

DO $qa$
DECLARE violations bigint;
BEGIN
    SELECT count(*) INTO violations FROM (
        SELECT supplier_id, external_id FROM source_products
        WHERE external_id_is_stable GROUP BY supplier_id, external_id HAVING count(*) > 1
    ) duplicate_ids;
    IF violations <> 0 THEN RAISE EXCEPTION 'DUPLICATE_STABLE_EXTERNAL_IDS=%', violations; END IF;

    SELECT count(*) INTO violations FROM (
        SELECT source_product_id FROM offers WHERE offer_kind='default'
        GROUP BY source_product_id HAVING count(*) > 1
    ) duplicate_offers;
    IF violations <> 0 THEN RAISE EXCEPTION 'DUPLICATE_DEFAULT_OFFERS=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
    LEFT JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
    WHERE s.code='partner_st' AND sp.active AND o.id IS NULL;
    IF violations <> 0 THEN RAISE EXCEPTION 'PARTNER_PRODUCTS_WITHOUT_DEFAULT_OFFER=%', violations; END IF;

    SELECT count(*) INTO violations FROM (
        SELECT source_product_id FROM product_matches WHERE is_current
        GROUP BY source_product_id HAVING count(*) > 1
    ) multiple_current;
    IF violations <> 0 THEN RAISE EXCEPTION 'INVALID_CURRENT_MATCH_MULTIPLICITY=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM source_products sp
    JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current AND pm.auto_accepted
    WHERE sp.catalog_product_id IS DISTINCT FROM pm.catalog_product_id;
    IF violations <> 0 THEN RAISE EXCEPTION 'IMPOSSIBLE_CATALOG_MAPPING=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM sterbrust_product_supplier_identifiers i
    JOIN suppliers s ON s.id=i.supplier_id
    WHERE i.supplier_id IS NOT NULL AND i.supplier_code <> s.code;
    IF violations <> 0 THEN RAISE EXCEPTION 'CROSS_SUPPLIER_IDENTIFIER_VIOLATIONS=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM sterbrust_product_supplier_identifiers
    WHERE supplier_code='partner_st' AND supplier_id IS NULL;
    IF violations <> 0 THEN RAISE EXCEPTION 'PARTNER_ST_IDENTIFIER_NULL_SUPPLIER_IDS=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM sterbrust_product_supplier_identifiers i
    JOIN suppliers s ON s.id=i.supplier_id
    WHERE i.supplier_code='partner_st' AND s.code<>'partner_st';
    IF violations <> 0 THEN RAISE EXCEPTION 'PARTNER_ST_IDENTIFIER_SUPPLIER_CODE_MISMATCH=%', violations; END IF;

    SELECT count(*) INTO violations
    FROM source_product_relations r
    JOIN source_products source ON source.id=r.from_source_product_id
    JOIN source_products target ON target.id=r.to_source_product_id
    WHERE r.supplier_id <> source.supplier_id OR r.supplier_id <> target.supplier_id;
    IF violations <> 0 THEN RAISE EXCEPTION 'CROSS_SUPPLIER_RELATION_VIOLATIONS=%', violations; END IF;

    SELECT count(*) INTO violations FROM product_properties p
    LEFT JOIN source_products sp ON sp.id=p.source_product_id WHERE sp.id IS NULL;
    IF violations <> 0 THEN RAISE EXCEPTION 'ORPHAN_PRODUCT_PROPERTIES=%', violations; END IF;

    SELECT count(*) INTO violations FROM product_media m
    LEFT JOIN source_products sp ON sp.id=m.source_product_id WHERE sp.id IS NULL;
    IF violations <> 0 THEN RAISE EXCEPTION 'ORPHAN_PRODUCT_MEDIA=%', violations; END IF;

    SELECT count(*) INTO violations FROM source_product_options o
    LEFT JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.id IS NULL;
    IF violations <> 0 THEN RAISE EXCEPTION 'ORPHAN_PRODUCT_OPTIONS=%', violations; END IF;

    SELECT count(*) INTO violations FROM crawl_runs cr
    JOIN suppliers s ON s.id=cr.supplier_id
    WHERE s.code='partner_st' AND cr.crawl_type='full';
    IF violations <> 2 THEN RAISE EXCEPTION 'PARTNER_ST_FULL_CRAWL_RUNS=% expected 2', violations; END IF;

    SELECT count(*) INTO violations FROM crawl_runs cr
    JOIN suppliers s ON s.id=cr.supplier_id
    WHERE s.code='partner_st' AND (cr.status<>'succeeded' OR cr.error_count<>0
       OR cr.discovered_count<>cr.fetched_count OR cr.anomalous_discovery OR NOT cr.deactivation_allowed);
    IF violations <> 0 THEN RAISE EXCEPTION 'UNHEALTHY_PARTNER_ST_CRAWL_RUNS=%', violations; END IF;

    SELECT count(*) INTO violations FROM (
        SELECT sp.id FROM source_products sp
        JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN source_product_urls u ON u.source_product_id=sp.id
        WHERE s.code='partner_st' GROUP BY sp.id HAVING count(u.id)=0
    ) missing_urls;
    IF violations <> 0 THEN RAISE EXCEPTION 'PARTNER_ST_PRODUCTS_WITHOUT_URL_HISTORY=%', violations; END IF;

    SELECT count(*) INTO violations FROM product_matches
    WHERE auto_accepted AND match_method IN ('NAME_CANDIDATE_ONLY','NO_IDENTITY_EVIDENCE');
    IF violations <> 0 THEN RAISE EXCEPTION 'FUZZY_AUTO_MATCHES=%', violations; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
        WHERE s.code='partner_st' AND sp.external_id='297' AND sp.sku='508002'
          AND pm.status='EXACT_MATCH' AND pm.match_method='EXISTING_LINK'
          AND pm.auto_accepted AND sp.catalog_product_id=pm.catalog_product_id
    ) THEN RAISE EXCEPTION 'PP800F_PERSISTENT_MAPPING_STABLE=FAIL'; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
        WHERE s.code='partner_st' AND sp.sku='211301' AND pm.status='CONFLICT'
          AND NOT pm.auto_accepted
    ) THEN RAISE EXCEPTION 'MRX3_MRX4_CONFLICT_PRESERVED=FAIL'; END IF;

    RAISE NOTICE 'Stage 3B database integrity assertions passed.';
END
$qa$;

ROLLBACK;
