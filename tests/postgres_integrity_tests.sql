BEGIN;

DO $qa$
DECLARE
    supplier_a bigint;
    supplier_b bigint;
    crawl_a bigint;
    crawl_b bigint;
    product_a bigint;
    product_a2 bigint;
    product_b bigint;
    offer_a bigint;
    offer_b bigint;
    category_a bigint;
    category_b bigint;
    child_a bigint;
    path_a bigint;
    rejected boolean;
BEGIN
    INSERT INTO suppliers (code, name, base_url, adapter_name)
    VALUES ('qa_supplier_a', 'QA Supplier A', 'https://qa-a.invalid', 'qa')
    RETURNING id INTO supplier_a;

    INSERT INTO suppliers (code, name, base_url, adapter_name)
    VALUES ('qa_supplier_b', 'QA Supplier B', 'https://qa-b.invalid', 'qa')
    RETURNING id INTO supplier_b;

    INSERT INTO crawl_runs (supplier_id, crawl_type)
    VALUES (supplier_a, 'identity_validation') RETURNING id INTO crawl_a;
    INSERT INTO crawl_runs (supplier_id, crawl_type)
    VALUES (supplier_b, 'identity_validation') RETURNING id INTO crawl_b;

    INSERT INTO source_products (supplier_id, name, source_url, first_seen_at, last_seen_at)
    VALUES (supplier_a, 'A1', 'https://qa-a.invalid/a1', now(), now()) RETURNING id INTO product_a;
    INSERT INTO source_products (supplier_id, name, source_url, first_seen_at, last_seen_at)
    VALUES (supplier_a, 'A2', 'https://qa-a.invalid/a2', now(), now()) RETURNING id INTO product_a2;
    INSERT INTO source_products (supplier_id, name, source_url, first_seen_at, last_seen_at)
    VALUES (supplier_b, 'B1', 'https://qa-b.invalid/b1', now(), now()) RETURNING id INTO product_b;

    INSERT INTO offers (supplier_id, source_product_id, first_seen_at, last_seen_at)
    VALUES (supplier_a, product_a, now(), now()) RETURNING id INTO offer_a;
    INSERT INTO offers (supplier_id, source_product_id, first_seen_at, last_seen_at)
    VALUES (supplier_b, product_b, now(), now()) RETURNING id INTO offer_b;

    INSERT INTO supplier_categories (supplier_id, name, first_seen_at, last_seen_at)
    VALUES (supplier_a, 'Category A', now(), now()) RETURNING id INTO category_a;
    INSERT INTO supplier_categories (supplier_id, name, first_seen_at, last_seen_at)
    VALUES (supplier_b, 'Category B', now(), now()) RETURNING id INTO category_b;

    -- 1. A product cannot be assigned to B's category.
    rejected := false;
    BEGIN
        INSERT INTO source_product_categories
            (supplier_id, source_product_id, category_id, first_seen_at, last_seen_at)
        VALUES (supplier_a, product_a, category_b, now(), now());
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-01 cross-supplier product/category accepted'; END IF;
    -- Valid control 01: A product in A category.
    INSERT INTO source_product_categories
        (supplier_id, source_product_id, category_id, first_seen_at, last_seen_at)
    VALUES (supplier_a, product_a, category_a, now(), now());

    -- 2. A category cannot use B's category as its parent.
    rejected := false;
    BEGIN
        INSERT INTO supplier_categories (supplier_id, name, parent_id, depth, first_seen_at, last_seen_at)
        VALUES (supplier_a, 'Invalid child', category_b, 1, now(), now());
        SET CONSTRAINTS supplier_categories_parent_supplier_fk IMMEDIATE;
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    SET CONSTRAINTS supplier_categories_parent_supplier_fk DEFERRED;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-02 cross-supplier category parent accepted'; END IF;
    -- Valid control 02: A category parent in A.
    INSERT INTO supplier_categories (supplier_id, name, parent_id, depth, first_seen_at, last_seen_at)
    VALUES (supplier_a, 'Valid child', category_a, 1, now(), now()) RETURNING id INTO child_a;

    INSERT INTO product_category_paths (supplier_id, source_product_id, observed_at)
    VALUES (supplier_a, product_a, now()) RETURNING id INTO path_a;

    -- 3. A breadcrumb path cannot contain B's category.
    rejected := false;
    BEGIN
        INSERT INTO product_category_path_nodes (supplier_id, path_id, position, category_id, raw_name)
        VALUES (supplier_a, path_a, 0, category_b, 'Invalid B node');
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-03 cross-supplier breadcrumb node accepted'; END IF;
    -- Valid control 03: A breadcrumb node in an A path.
    INSERT INTO product_category_path_nodes (supplier_id, path_id, position, category_id, raw_name)
    VALUES (supplier_a, path_a, 0, child_a, 'Valid A node');

    -- 4. A source-product relation cannot point from A to B.
    rejected := false;
    BEGIN
        INSERT INTO source_product_relations
            (supplier_id, from_source_product_id, to_source_product_id, relation_type, first_seen_at, last_seen_at)
        VALUES (supplier_a, product_a, product_b, 'related', now(), now());
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-04 cross-supplier product relation accepted'; END IF;
    -- Valid control 04: A-to-A product relation.
    INSERT INTO source_product_relations
        (supplier_id, from_source_product_id, to_source_product_id, relation_type, first_seen_at, last_seen_at)
    VALUES (supplier_a, product_a, product_a2, 'related', now(), now());

    -- 5. A crawl run for A cannot attach B's product.
    rejected := false;
    BEGIN
        INSERT INTO crawl_run_items (crawl_run_id, supplier_id, source_product_id, discovered_url, item_status)
        VALUES (crawl_a, supplier_a, product_b, 'https://qa-b.invalid/b1', 'fetched_unchanged');
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-05 crawl A accepted product B'; END IF;
    -- Valid control 05: A crawl item references A product.
    INSERT INTO crawl_run_items (crawl_run_id, supplier_id, source_product_id, discovered_url, item_status)
    VALUES (crawl_a, supplier_a, product_a, 'https://qa-a.invalid/a1', 'fetched_unchanged');

    -- 6. A crawl error for A cannot reference either B's product or B's offer.
    rejected := false;
    BEGIN
        INSERT INTO crawl_errors (crawl_run_id, supplier_id, source_product_id, url, error_type, message)
        VALUES (crawl_a, supplier_a, product_b, 'https://qa-b.invalid/b1', 'qa', 'invalid product');
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-06a crawl error A accepted product B'; END IF;
    rejected := false;
    BEGIN
        INSERT INTO crawl_errors (crawl_run_id, supplier_id, offer_id, url, error_type, message)
        VALUES (crawl_a, supplier_a, offer_b, 'https://qa-b.invalid/b1', 'qa', 'invalid offer');
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-06b crawl error A accepted offer B'; END IF;
    -- Valid control 06: A crawl error references A product and A offer.
    INSERT INTO crawl_errors (crawl_run_id, supplier_id, source_product_id, url, error_type, message)
    VALUES (crawl_a, supplier_a, product_a, 'https://qa-a.invalid/a1', 'qa', 'valid product');
    INSERT INTO crawl_errors (crawl_run_id, supplier_id, offer_id, url, error_type, message)
    VALUES (crawl_a, supplier_a, offer_a, 'https://qa-a.invalid/a1', 'qa', 'valid offer');

    -- 7. Change history for A cannot reference either B's product or B's offer.
    rejected := false;
    BEGIN
        INSERT INTO entity_change_history
            (crawl_run_id, supplier_id, source_product_id, field_group, old_value, new_value)
        VALUES (crawl_a, supplier_a, product_b, 'identity', '1'::jsonb, '2'::jsonb);
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-07a history A accepted product B'; END IF;
    rejected := false;
    BEGIN
        INSERT INTO entity_change_history
            (crawl_run_id, supplier_id, offer_id, field_group, old_value, new_value)
        VALUES (crawl_a, supplier_a, offer_b, 'commercial', '1'::jsonb, '2'::jsonb);
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-07b history A accepted offer B'; END IF;
    -- Valid control 07: A history row references A product and A offer.
    INSERT INTO entity_change_history
        (crawl_run_id, supplier_id, source_product_id, field_group, old_value, new_value)
    VALUES (crawl_a, supplier_a, product_a, 'identity', '1'::jsonb, '2'::jsonb);
    INSERT INTO entity_change_history
        (crawl_run_id, supplier_id, offer_id, field_group, old_value, new_value)
    VALUES (crawl_a, supplier_a, offer_a, 'commercial', '1'::jsonb, '2'::jsonb);

    -- 8. A supplier-scoped feed must identify its supplier.
    rejected := false;
    BEGIN
        INSERT INTO feed_runs (feed_scope, supplier_id, feed_type)
        VALUES ('supplier', NULL, 'qa');
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-08 supplier feed without supplier accepted'; END IF;
    -- Valid control 08: supplier feed identifies A.
    INSERT INTO feed_runs (feed_scope, supplier_id, feed_type)
    VALUES ('supplier', supplier_a, 'qa');

    -- 9. A combined feed must not identify one supplier.
    rejected := false;
    BEGIN
        INSERT INTO feed_runs (feed_scope, supplier_id, feed_type)
        VALUES ('combined', supplier_a, 'qa');
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'QA-09 combined feed with supplier accepted'; END IF;
    -- Valid control 09: combined feed has no supplier.
    INSERT INTO feed_runs (feed_scope, supplier_id, feed_type)
    VALUES ('combined', NULL, 'qa');

    RAISE NOTICE 'All 9 supplier-isolation cases and their valid same-supplier controls passed.';
END
$qa$;

ROLLBACK;
