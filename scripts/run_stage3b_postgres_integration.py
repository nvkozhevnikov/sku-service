#!/usr/bin/env python3
"""Guarded LIVE -> external PostgreSQL -> matching -> RUN2 integration gate."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.crawl import LiveCrawlResult, run_partner_st_live
from universal_supplier.change_detection import card_hashes
from universal_supplier.matching_service import load_registry_from_postgres, match_cards
from universal_supplier.postgres import PostgresConfig, PostgresRepository

REPORTS = ROOT / "reports"
MATCH_FIELDS = ["source_external_id", "SKU", "name", "brand", "model", "status",
                "match_method", "confidence_score", "sterbrust_product_id", "conflict_class",
                "warnings", "data_quality_flags", "conflicts", "evidence", "auto_accepted"]
INTEGRITY_SQL_FILES = ("postgres_integrity_tests.sql", "postgres_stage3a3_integrity_tests.sql",
                       "postgres_stage3b_integrity_tests.sql")


def read_psycopg_sql(path: Path) -> str:
    """Read pure PostgreSQL SQL and reject every psql client meta-command."""
    sql = path.read_text(encoding="utf-8")
    commands = re.findall(r"(?m)^\s*(\\\S+.*)$", sql)
    if commands:
        raise ValueError(f"Unsupported psql meta-command in {path.name}: {commands[0]}")
    return sql


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def previous_discovery_baseline() -> int | None:
    """Use the last recorded healthy crawl count; never hardcode catalog size."""
    for name in ("partner_st_run2_metadata.json", "partner_st_run1_metadata.json"):
        path = REPORTS / name
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8")).get("discovered_count")
            if isinstance(value, int) and value > 0:
                return value
    return None


def write_crawl_artifacts(label: str, crawl: LiveCrawlResult) -> dict:
    fields = (list(crawl.rows[0]) if crawl.rows else ["position", "requested_url", "final_url",
              "http_status", "redirect_count", "attempt_count", "external_id", "sku", "name",
              "result", "error"])
    with (REPORTS / f"PARTNER_ST_FULL_CRAWL_{label}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(crawl.rows)
    with (REPORTS / f"PARTNER_ST_PARSED_{label}.jsonl").open("w", encoding="utf-8") as handle:
        for card in crawl.cards:
            handle.write(json.dumps(card.as_jsonable(), ensure_ascii=False, sort_keys=True) + "\n")
    ids = [card.external_id for card in crawl.cards]
    metadata = {
        "run": label, "started_at": crawl.started_at, "finished_at": crawl.finished_at,
        "transport": "direct_http", "playwright_used": False, "proxy_used": False,
        **crawl.discovery_metadata, "discovered_count": len(crawl.discovered_urls),
        "baseline_discovered_count": crawl.health.baseline_discovered_count,
        "discovery_ratio": crawl.health.discovery_ratio,
        "anomalous_discovery": crawl.health.anomalous_discovery,
        "deactivation_allowed": crawl.health.deactivation_allowed,
        "fetched_count": len(crawl.cards), "http_200": sum(row["http_status"] == 200 for row in crawl.rows),
        "redirects": sum(int(row["redirect_count"]) for row in crawl.rows),
        "errors": len(crawl.errors), "unique_product_ids": len(set(ids)),
        "duplicate_product_ids": len(ids) - len(set(ids)), "sterbrust_write_methods_used": 0,
    }
    (REPORTS / f"partner_st_{label.lower()}_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def write_matches(label: str, rows: list[dict]) -> None:
    with (REPORTS / f"PARTNER_ST_FULL_MATCHING_{label}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def require_healthy_full_crawl(crawl: LiveCrawlResult) -> None:
    ids = [card.external_id for card in crawl.cards]
    if (crawl.errors or crawl.health.anomalous_discovery
            or len(crawl.cards) != len(crawl.discovered_urls)
            or len(ids) != len(set(ids))):
        raise RuntimeError("Live full crawl is not healthy enough for Stage 3B closure")


def run_one(repo: PostgresRepository, label: str, baseline: int | None, registry):
    started = datetime.now(timezone.utc).isoformat()
    crawl_run_id = repo.start_crawl_run(started_at=started, baseline_discovered_count=baseline,
                                        metadata={"run": label, "source": "live_dynamic_sitemap_http"})
    try:
        crawl = run_partner_st_live(baseline=baseline)
        meta = write_crawl_artifacts(label, crawl)
        for error in crawl.errors:
            repo.record_crawl_error(crawl_run_id, url=error.url, error_type=error.error_type,
                                    message=error.message, http_status=error.http_status)
        counts = repo.ingest(list(crawl.cards), crawl.finished_at, crawl_run_id=crawl_run_id,
                             discovery_health=crawl.health,
                             discovered_urls=list(crawl.discovered_urls), error_count=len(crawl.errors))
        require_healthy_full_crawl(crawl)
        existing_links = {} if label == "RUN1" else repo.load_existing_links()
        matches = match_cards(crawl.cards, registry, existing_links)
        for decision in matches:
            repo.persist_match(decision, crawl.finished_at)
        write_matches(label, matches)
        return crawl, meta, counts, matches
    except Exception as error:
        repo.fail_crawl_run(crawl_run_id, finished_at=datetime.now(timezone.utc).isoformat(), message=str(error))
        raise


def scalar_assertions(repo: PostgresRepository) -> dict[str, int]:
    queries = {
        "DUPLICATE_STABLE_EXTERNAL_IDS": "SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products WHERE external_id_is_stable GROUP BY supplier_id,external_id HAVING count(*)>1)x",
        "DUPLICATE_DEFAULT_OFFERS_PER_SOURCE_PRODUCT": "SELECT count(*) FROM (SELECT source_product_id FROM offers WHERE offer_kind='default' GROUP BY source_product_id HAVING count(*)>1)x",
        "CROSS_SUPPLIER_RELATION_VIOLATIONS": "SELECT count(*) FROM source_product_relations r JOIN source_products a ON a.id=r.from_source_product_id JOIN source_products b ON b.id=r.to_source_product_id WHERE r.supplier_id<>a.supplier_id OR r.supplier_id<>b.supplier_id",
        "CROSS_SUPPLIER_IDENTIFIER_VIOLATIONS": "SELECT count(*) FROM sterbrust_product_supplier_identifiers i JOIN suppliers s ON s.id=i.supplier_id WHERE i.supplier_code<>s.code",
        "PARTNER_ST_IDENTIFIER_NULL_SUPPLIER_IDS": "SELECT count(*) FROM sterbrust_product_supplier_identifiers WHERE supplier_code='partner_st' AND supplier_id IS NULL",
        "PARTNER_ST_IDENTIFIER_SUPPLIER_CODE_MISMATCH": "SELECT count(*) FROM sterbrust_product_supplier_identifiers i JOIN suppliers s ON s.id=i.supplier_id WHERE i.supplier_code='partner_st' AND s.code<>'partner_st'",
        "ORPHAN_SOURCE_PRODUCTS": "SELECT count(*) FROM source_products sp LEFT JOIN suppliers s ON s.id=sp.supplier_id WHERE s.id IS NULL",
        "ORPHAN_OFFERS": "SELECT count(*) FROM offers o LEFT JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.id IS NULL",
        "ORPHAN_PROPERTIES": "SELECT count(*) FROM product_properties p LEFT JOIN source_products sp ON sp.id=p.source_product_id WHERE sp.id IS NULL",
        "ORPHAN_MEDIA": "SELECT count(*) FROM product_media m LEFT JOIN source_products sp ON sp.id=m.source_product_id WHERE sp.id IS NULL",
        "ORPHAN_OPTIONS": "SELECT count(*) FROM source_product_options o LEFT JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.id IS NULL",
        "INVALID_CURRENT_MATCH_MULTIPLICITY": "SELECT count(*) FROM (SELECT source_product_id FROM product_matches WHERE is_current GROUP BY source_product_id HAVING count(*)>1)x",
        "IMPOSSIBLE_CATALOG_MAPPING": "SELECT count(*) FROM source_products sp JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current AND pm.auto_accepted WHERE sp.catalog_product_id IS DISTINCT FROM pm.catalog_product_id",
        "FUZZY_AUTO_MATCHES": "SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method IN ('NAME_CANDIDATE_ONLY','NO_IDENTITY_EVIDENCE')",
        "CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES": "SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method='SUPPLIER_ARTICLE_BRAND'",
        "FALSE_EXISTING_LINK_CONFLICTS": "SELECT count(*) FROM product_matches current_match JOIN product_matches previous_match ON previous_match.id=current_match.previous_match_id WHERE current_match.is_current AND current_match.status='CONFLICT' AND previous_match.auto_accepted AND coalesce(current_match.evidence->>'conflict_class','')<>'IDENTITY_CONFLICT'",
    }
    results = {}
    with repo.connection.cursor() as cursor:
        for name, query in queries.items():
            cursor.execute(query)
            results[name] = cursor.fetchone()[0]
    return results


def verify_after_reopen(repo: PostgresRepository, expected_counts: dict[str, int]) -> dict:
    """Prove durability through a new independent PostgreSQL connection."""
    gate = repo.gate()
    counts = repo.table_counts()
    assertions = scalar_assertions(repo)
    with repo.connection.cursor() as cursor:
        cursor.execute("""SELECT count(*) FROM crawl_runs cr JOIN suppliers s ON s.id=cr.supplier_id
                          WHERE s.code='partner_st' AND cr.crawl_type='full'""")
        crawl_runs = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM crawl_run_items i
                          WHERE i.crawl_run_id=(SELECT cr.id FROM crawl_runs cr
                            JOIN suppliers s ON s.id=cr.supplier_id
                            WHERE s.code='partner_st' AND cr.crawl_type='full'
                            ORDER BY cr.started_at DESC, cr.id DESC LIMIT 1)""")
        run2_items = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                          WHERE s.code='partner_st'""")
        partner_products = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id
                          WHERE s.code='partner_st' AND o.offer_kind='default'""")
        default_offers = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id
                          JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='partner_st' AND pm.is_current""")
        current_matches = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                          WHERE s.code='partner_st' AND sp.catalog_product_id IS NOT NULL""")
        accepted_links = cursor.fetchone()[0]
        cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                          JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
                          WHERE s.code='partner_st' AND sp.external_id='297' AND sp.sku='508002'
                            AND pm.status='EXACT_MATCH' AND pm.match_method='EXISTING_LINK'
                            AND pm.auto_accepted AND sp.catalog_product_id=pm.catalog_product_id""")
        pp800f_ok = cursor.fetchone()[0] == 1
        cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                          JOIN product_matches pm ON pm.source_product_id=sp.id
                          WHERE s.code='partner_st' AND sp.external_id='297' AND sp.sku='508002'
                            AND pm.status='EXACT_MATCH' AND pm.match_method='SUPPLIER_ARTICLE_EXACT'
                            AND pm.auto_accepted""")
        pp800f_run1_history_ok = cursor.fetchone()[0] == 1
        cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                          JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
                          WHERE s.code='partner_st' AND sp.sku='211301'
                            AND pm.status='CONFLICT' AND NOT pm.auto_accepted""")
        mrx_conflict_ok = cursor.fetchone()[0] == 1
    count_mismatches = {table: {"expected": expected, "actual": counts.get(table)}
                        for table, expected in expected_counts.items() if counts.get(table) != expected}
    failures = []
    if gate["select_1"] is not True:
        failures.append("post_close_select_1")
    if crawl_runs != 2:
        failures.append(f"crawl_runs={crawl_runs}")
    if partner_products <= 0 or default_offers <= 0 or run2_items <= 0 or current_matches <= 0 or accepted_links <= 0:
        failures.append("required persisted rows missing")
    if not pp800f_ok:
        failures.append("PP800F_PERSISTENT_MAPPING_STABLE")
    if not pp800f_run1_history_ok:
        failures.append("PP800F_RUN1_ACCEPTED_MATCH_HISTORY")
    if not mrx_conflict_ok:
        failures.append("MRX3_MRX4_CONFLICT_PRESERVED")
    if any(assertions.values()):
        failures.append("post_close_integrity_assertions")
    if count_mismatches:
        failures.append("post_close_count_mismatch")
    if failures:
        raise RuntimeError("Post-close verification failed: " + ", ".join(failures))
    return {
        "post_close_verification": "PASS", "post_close_select_1": "PASS",
        "post_close_db_counts": counts, "post_close_current_matches": current_matches,
        "post_close_crawl_runs": crawl_runs, "post_close_run2_crawl_run_items": run2_items,
        "post_close_partner_source_products": partner_products,
        "post_close_partner_default_offers": default_offers,
        "post_close_accepted_catalog_links": accepted_links,
        "post_close_entity_change_history": counts["entity_change_history"],
        "post_close_assertions": assertions,
        "post_close_pp800f_persistent_mapping_stable": "PASS",
        "post_close_pp800f_run1_accepted_match_history": "PASS",
        "post_close_mrx3_mrx4_conflict_preserved": "PASS",
    }


def main() -> None:
    if os.environ.get("STAGE3B_DB_CONFIRM") != "YES":
        raise SystemExit("Set STAGE3B_DB_CONFIRM=YES only for the explicitly selected external integration database")
    config = PostgresConfig.from_env()
    baseline = json.loads((ROOT / "stage3a3_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    changed = [name for name, expected in baseline["files"].items()
               if sha256(ROOT / "migrations" / name) != expected]
    if changed:
        raise SystemExit("Immutable migration checksum mismatch: " + ", ".join(changed))
    repo = PostgresRepository(config)
    preclose_result = None
    try:
        gate = repo.gate()
        with repo.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname=current_schema()")
            if cursor.fetchone()[0] != 0:
                raise SystemExit("Integration schema must be empty; refusing to alter a non-empty database")
        for migration in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")):
            with repo.connection.cursor() as cursor:
                cursor.execute(migration.read_text(encoding="utf-8"))
        repo.ensure_partner_supplier()

        # RUN1 is genuinely live and is persisted before Sterbrust matching.
        run1_baseline = previous_discovery_baseline()
        run1_started = datetime.now(timezone.utc).isoformat()
        run1_id = repo.start_crawl_run(started_at=run1_started,
                                       baseline_discovered_count=run1_baseline,
                                       metadata={"run": "RUN1", "source": "live_dynamic_sitemap_http"})
        try:
            run1_crawl = run_partner_st_live(baseline=run1_baseline)
        except Exception as error:
            repo.fail_crawl_run(run1_id, finished_at=datetime.now(timezone.utc).isoformat(), message=str(error))
            raise
        run1_meta = write_crawl_artifacts("RUN1", run1_crawl)
        for error in run1_crawl.errors:
            repo.record_crawl_error(run1_id, url=error.url, error_type=error.error_type,
                                    message=error.message, http_status=error.http_status)
        run1 = repo.ingest(list(run1_crawl.cards), run1_crawl.finished_at, crawl_run_id=run1_id,
                           discovery_health=run1_crawl.health,
                           discovered_urls=list(run1_crawl.discovered_urls), error_count=len(run1_crawl.errors))
        require_healthy_full_crawl(run1_crawl)
        sterbrust_loaded = repo.load_sterbrust_snapshot(
            REPORTS / "STERBRUST_REGISTRY.jsonl",
            REPORTS / "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv")
        with repo.connection.cursor() as cursor:
            cursor.execute((ROOT / "scripts" / "bootstrap_sterbrust_catalog.sql").read_text(encoding="utf-8"))
        repo.sync_sterbrust_identifier_catalog_links()
        registry = load_registry_from_postgres(repo.connection)
        run1_matches = match_cards(run1_crawl.cards, registry, {})
        for row in run1_matches:
            repo.persist_match(row, run1_crawl.finished_at)
        write_matches("RUN1", run1_matches)
        db_after_run1 = repo.table_counts()

        # RUN2 existing links are read only from source_products in this database.
        run2_crawl, run2_meta, run2, run2_matches = run_one(
            repo, "RUN2", len(run1_crawl.discovered_urls), registry)
        db_after_run2 = repo.table_counts()
        assertions = scalar_assertions(repo)
        if any(assertions.values()):
            raise RuntimeError("PostgreSQL scalar integrity assertions failed: " + json.dumps(assertions))
        for test in INTEGRITY_SQL_FILES:
            with repo.connection.cursor() as cursor:
                cursor.execute(read_psycopg_sql(ROOT / "tests" / test))

        by_id1 = {row["source_external_id"]: row for row in run1_matches}
        by_id2 = {row["source_external_id"]: row for row in run2_matches}
        false_existing = sum(
            row["auto_accepted"] == "TRUE"
            and by_id2.get(key, {}).get("status") == "CONFLICT"
            and by_id2.get(key, {}).get("conflict_class") != "IDENTITY_CONFLICT"
            for key, row in by_id1.items())
        pp1, pp2 = by_id1["297"], by_id2["297"]
        mrx = next(row for row in run2_matches if row["SKU"] == "211301")
        if (pp1["status"], pp1["match_method"]) != ("EXACT_MATCH", "SUPPLIER_ARTICLE_EXACT"):
            raise RuntimeError("PP-800F RUN1 regression failed")
        if (pp2["status"], pp2["match_method"]) != ("EXACT_MATCH", "EXISTING_LINK"):
            raise RuntimeError("PP-800F RUN2 persistent-link regression failed")
        if mrx["status"] != "CONFLICT" or mrx["auto_accepted"] != "FALSE":
            raise RuntimeError("MR-X3/MR-X4 conflict regression failed")
        if false_existing:
            raise RuntimeError(f"FALSE_EXISTING_LINK_CONFLICTS={false_existing}")
        cards1 = {card.external_id: card_hashes(card) for card in run1_crawl.cards}
        cards2 = {card.external_id: card_hashes(card) for card in run2_crawl.cards}
        source_diff = {
            "new": sorted(set(cards2) - set(cards1), key=int),
            "missing": sorted(set(cards1) - set(cards2), key=int),
            "changed": sorted((key for key in set(cards1) & set(cards2) if cards1[key] != cards2[key]), key=int),
        }
        site_unchanged = not any(source_diff.values())
        history_delta = db_after_run2["entity_change_history"] - db_after_run1["entity_change_history"]
        if site_unchanged and any((run2.new_products, run2.changed_products, run2.new_offers,
                                   run2.changed_offers, history_delta)):
            raise RuntimeError("Unchanged RUN2 produced non-idempotent products/offers/history")
        idempotency_qa = "PASS" if site_unchanged else "PASS_WITH_RECORDED_LIVE_SOURCE_CHANGES"
        with (REPORTS / "POSTGRES_TABLE_COUNTS.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["table", "after_run1", "after_run2"])
            writer.writeheader()
            writer.writerows({"table": table, "after_run1": db_after_run1[table],
                              "after_run2": db_after_run2[table]} for table in db_after_run2)
        preclose_result = {
            "integration_timestamp": datetime.now(timezone.utc).isoformat(),
            **gate, "postgres_external": True, "postgres_in_docker": False,
            "connection_autocommit": repo.connection.autocommit,
            "migrations_001_010": "PASS", "migration_011_optional_sterbrust_url": "PASS",
            "integrity_tests": "PASS",
            "credentials_recorded": False, "sterbrust_products_loaded": sterbrust_loaded,
            "live_run1": run1_meta, "live_run2": run2_meta,
            "db_run1": vars(run1), "db_run2": vars(run2),
            "db_counts_after_run1": db_after_run1, "db_counts_after_run2": db_after_run2,
            "assertions": assertions, "false_existing_link_conflicts": false_existing,
            "source_diff": source_diff, "idempotency_qa": idempotency_qa,
            "change_history_run2_delta": history_delta,
            "matching_run1": dict(Counter(row["status"] for row in run1_matches)),
            "matching_run2": dict(Counter(row["status"] for row in run2_matches)),
            "pp800f_run1": f"{pp1['status']} / {pp1['match_method']}",
            "pp800f_run2": f"{pp2['status']} / {pp2['match_method']}",
            "mrx3_mrx4": mrx["status"],
        }
    finally:
        repo.close()

    # PASS is impossible until a new independent connection proves that every
    # count, current match and accepted mapping survived the original close.
    verifier = PostgresRepository(config)
    try:
        durability = verify_after_reopen(verifier, preclose_result["db_counts_after_run2"])
    finally:
        verifier.close()
    result = {"state": "PASS", **preclose_result, **durability}
    (REPORTS / "STAGE3B_POSTGRES_INTEGRATION_QA.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "POSTGRES_INTEGRITY_RESULTS.txt").write_text(
        "POSTGRES_INTEGRATION_QA = PASS\nMIGRATIONS_001_010 = PASS\n"
        "MIGRATION_011_OPTIONAL_STERBRUST_URL = PASS\n"
        "POSTGRES_INTEGRITY_TESTS = PASS\nPOST_CLOSE_VERIFICATION = PASS\n" +
        "\n".join(f"{key} = {value}" for key, value in result["post_close_assertions"].items()) + "\n",
        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
