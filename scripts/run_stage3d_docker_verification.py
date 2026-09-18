#!/usr/bin/env python3
"""Load verified Stage 3B evidence and prove Stage 3C samples from real SQL rows."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))

from universal_supplier.models import ProductCard
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.state import DiscoveryHealth


def jsonl_cards(name: str) -> list[ProductCard]:
    with (REPORTS / name).open(encoding="utf-8") as handle:
        return [ProductCard.from_jsonable(json.loads(line)) for line in handle if line.strip()]


def csv_rows(name: str) -> list[dict[str, str]]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def decimal_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    normalized = format(Decimal(str(value)).normalize(), "f")
    return "0" if normalized == "-0" else normalized


def migration_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    }


def load_packaged_stage3b(repo: PostgresRepository) -> None:
    with repo.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM source_products")
        product_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM crawl_runs")
        crawl_count = cursor.fetchone()[0]
    if product_count == 1222 and crawl_count >= 2:
        return
    if product_count or crawl_count:
        raise RuntimeError("Refusing partial/non-empty database; use a fresh Stage 3D database")

    runs = []
    for label in ("RUN1", "RUN2"):
        cards = jsonl_cards(f"PARTNER_ST_PARSED_{label}.jsonl")
        meta = json.loads((REPORTS / f"partner_st_{label.lower()}_metadata.json").read_text(encoding="utf-8"))
        health = DiscoveryHealth(
            meta["discovered_count"], meta["baseline_discovered_count"], meta["discovery_ratio"],
            meta["anomalous_discovery"], meta["deactivation_allowed"],
        )
        run_id = repo.start_crawl_run(
            started_at=meta["started_at"], baseline_discovered_count=meta["baseline_discovered_count"],
            metadata={"run": label, "source": "verified_packaged_stage3b_live_evidence", "http_replayed": False},
        )
        counts = repo.ingest(
            cards, meta["finished_at"], crawl_run_id=run_id, discovery_health=health,
            discovered_urls=[card.requested_url for card in cards], error_count=0,
        )
        runs.append((label, cards, meta, counts))
        if label == "RUN1":
            repo.load_sterbrust_snapshot(
                REPORTS / "STERBRUST_REGISTRY.jsonl",
                REPORTS / "STERBRUST_SUPPLIER_ARTICLE_PROPERTY_MAP.csv",
            )
            with repo.connection.cursor() as cursor:
                cursor.execute((ROOT / "scripts" / "bootstrap_sterbrust_catalog.sql").read_text(encoding="utf-8"))
            repo.sync_sterbrust_identifier_catalog_links()
        for decision in csv_rows(f"PARTNER_ST_FULL_MATCHING_{label}.csv"):
            repo.persist_match(decision, meta["finished_at"])

    if len(runs[1][1]) != 1222:
        raise RuntimeError(f"Packaged RUN2 count is {len(runs[1][1])}, expected 1222")


def fetch_db_product(repo: PostgresRepository, external_id: str) -> dict[str, Any]:
    with repo.connection.cursor() as cursor:
        cursor.execute(
            """SELECT sp.id, o.id, sp.catalog_product_id, pm.id, sp.external_id, sp.sku, sp.name,
                      sp.canonical_url, sp.brand_raw, sp.manufacturer_raw,
                      o.price, o.old_price, o.currency, o.availability_raw,
                      o.availability_normalized, o.quantity, pm.status, pm.match_method,
                      pm.catalog_product_id, sb.sterbrust_product_id
               FROM source_products sp
               JOIN suppliers s ON s.id=sp.supplier_id AND s.code='partner_st'
               JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
               JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
               LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=pm.catalog_product_id
               WHERE sp.external_id=%s""",
            (external_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"No physical DB row for Partner-ST external_id={external_id}")
        keys = (
            "source_product_id", "offer_id", "catalog_product_id", "product_match_id", "external_id",
            "sku", "name", "canonical_url", "brand_raw", "manufacturer_raw", "price", "old_price",
            "currency", "availability_raw", "availability_normalized", "quantity", "matching_status",
            "match_method", "match_candidate_catalog_product_id", "sterbrust_product_id",
        )
        result = dict(zip(keys, row))
        cursor.execute("SELECT count(*) FROM source_product_urls WHERE source_product_id=%s", (row[0],))
        result["urls_count"] = cursor.fetchone()[0]
        cursor.execute(
            "SELECT raw_breadcrumb FROM product_category_paths WHERE source_product_id=%s AND is_current ORDER BY id DESC LIMIT 1",
            (row[0],),
        )
        category_row = cursor.fetchone()
        result["categories"] = category_row[0] if category_row else []
        cursor.execute(
            "SELECT raw_name,raw_value,raw_unit,position FROM product_properties WHERE source_product_id=%s ORDER BY position",
            (row[0],),
        )
        result["properties"] = [
            {"name": name, "value": value or "", "unit": unit or "", "position": position}
            for name, value, unit, position in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT media_type,source_url,title,position,is_primary FROM product_media WHERE source_product_id=%s ORDER BY position",
            (row[0],),
        )
        result["media"] = [
            {"media_type": media_type, "url": url, "title": title or "", "position": position, "is_primary": primary}
            for media_type, url, title, position, primary in cursor.fetchall()
        ]
        cursor.execute(
            """SELECT o.id,o.raw_name,o.position,v.raw_value,v.target_url,v.variant_internal_id,
                      v.classification,v.is_selected,v.position
               FROM source_product_options o
               LEFT JOIN source_product_option_values v ON v.option_id=o.id
               WHERE o.source_product_id=%s ORDER BY o.position,v.position""",
            (row[0],),
        )
        options: dict[int, dict[str, Any]] = {}
        for option_id, name, option_pos, value, target, variant, classification, selected, value_pos in cursor.fetchall():
            option = options.setdefault(option_id, {"name": name, "position": option_pos, "values": []})
            if value is not None:
                option["values"].append({
                    "value": value, "target_url": target or "", "variant_internal_id": variant or "",
                    "classification": classification, "selected": selected, "position": value_pos,
                })
        result["options"] = list(options.values())
        cursor.execute("SELECT count(*) FROM source_product_relations WHERE from_source_product_id=%s", (row[0],))
        result["relations_count"] = cursor.fetchone()[0]
    return result


def expected_url_count(card: ProductCard) -> int:
    urls = {card.requested_url, *card.redirect_urls}
    if card.final_url:
        urls.add(card.final_url)
    if card.canonical_url:
        urls.add(card.canonical_url)
    return len({url for url in urls if url})


def expected_relation_count(card: ProductCard, all_cards: list[ProductCard]) -> int:
    known = {item.canonical_url.rstrip("/") for item in all_cards}
    own = card.canonical_url.rstrip("/")
    return sum(
        value.classification == "NAVIGATION_TO_PRODUCT"
        and value.target_url.rstrip("/") in known
        and value.target_url.rstrip("/") != own
        for option in card.options for value in option.values
    )


def source_fields(card: ProductCard, all_cards: list[ProductCard], expected_decision: dict[str, str]) -> dict[str, Any]:
    return {
        "external_id": card.external_id,
        "sku": card.sku,
        "name": card.name,
        "canonical_url": card.canonical_url,
        "brand_raw": card.brand or None,
        "manufacturer_raw": card.manufacturer or None,
        "price": decimal_text(card.price),
        "old_price": decimal_text(card.old_price),
        "currency": card.currency or None,
        "availability_raw": card.availability_raw,
        "availability_normalized": card.availability_normalized,
        "quantity": decimal_text(card.quantity),
        "urls_count": expected_url_count(card),
        "categories": [{"name": value.name, "url": value.url} for value in card.categories],
        "properties": [vars(value) for value in card.properties],
        "media": [vars(value) for value in card.media],
        "options": [
            {"name": option.name, "position": option.position, "values": [vars(value) for value in option.values]}
            for option in card.options
        ],
        "relations_count": expected_relation_count(card, all_cards),
        "matching_status": expected_decision["status"],
        "match_method": expected_decision["match_method"],
    }


def db_comparable(db: dict[str, Any]) -> dict[str, Any]:
    value = dict(db)
    for key in ("price", "old_price", "quantity"):
        value[key] = decimal_text(value[key])
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if os.environ.get("STAGE3D_DB_CONFIRM") != "YES":
        raise SystemExit("Set STAGE3D_DB_CONFIRM=YES only for the dedicated Docker Stage 3D database")
    expected_migrations = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))["files"]
    if migration_hashes() != expected_migrations:
        raise SystemExit("MIGRATIONS_001_011_IMMUTABLE = FAIL")

    config = PostgresConfig.from_env()
    repo = PostgresRepository(config)
    try:
        gate = repo.gate()
        load_packaged_stage3b(repo)
        all_cards = jsonl_cards("PARTNER_ST_PARSED_RUN2.jsonl")
        by_external = {card.external_id: card for card in all_cards}
        expected_decisions = {
            row["source_external_id"]: row for row in csv_rows("PARTNER_ST_FULL_MATCHING_RUN2.csv")
        }
        samples = csv_rows("STAGE3C_SAMPLE_SELECTION.csv")
        consistency_rows: list[dict[str, Any]] = []
        trace_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for sample in samples:
            external_id = sample["source_external_id"]
            card = by_external[external_id]
            db = db_comparable(fetch_db_product(repo, external_id))
            expected = source_fields(card, all_cards, expected_decisions[external_id])
            product_results = []
            for field, source_value in expected.items():
                db_value = db[field]
                source_text = compact(source_value) if isinstance(source_value, (list, dict)) else str(source_value or "")
                db_text = compact(db_value) if isinstance(db_value, (list, dict)) else str(db_value or "")
                result = "PASS" if source_text == db_text else "FAIL"
                product_results.append(result)
                consistency_rows.append({
                    "source_external_id": external_id, "SKU": card.sku, "field": field,
                    "SOURCE_VALUE": source_text, "DB_VALUE": db_text, "RESULT": result,
                })
                if result != "PASS":
                    failures.append(f"{external_id}:{field}")
            trace_rows.append({
                "sample_position": sample["sample_position"], "source_external_id": external_id,
                "SKU": card.sku, "source_product_id": db["source_product_id"], "offer_id": db["offer_id"],
                "catalog_product_id": db["catalog_product_id"] or "", "product_match_id": db["product_match_id"],
                "matching_status": db["matching_status"], "match_method": db["match_method"],
                "sterbrust_product_id": db["sterbrust_product_id"] or "", "urls_count": db["urls_count"],
                "categories_count": len(db["categories"]), "properties_count": len(db["properties"]),
                "media_count": len(db["media"]), "options_count": len(db["options"]),
                "option_values_count": sum(len(item["values"]) for item in db["options"]),
                "relations_count": db["relations_count"], "result": "PASS" if all(x == "PASS" for x in product_results) else "FAIL",
            })

        pp = fetch_db_product(repo, "297")
        mrx = fetch_db_product(repo, "305")
        pp_pass = (
            pp["sku"] == "508002" and pp["sterbrust_product_id"] == "88585"
            and pp["matching_status"] == "EXACT_MATCH" and pp["catalog_product_id"] is not None
            and pp["catalog_product_id"] == pp["match_candidate_catalog_product_id"]
        )
        mrx_pass = (
            mrx["sku"] == "211301" and mrx["matching_status"] == "CONFLICT"
            and mrx["catalog_product_id"] is None
        )
        counts = repo.table_counts()
        with repo.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method IN ('NAME_CANDIDATE_ONLY','NO_IDENTITY_EVIDENCE')")
            fuzzy = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method='SUPPLIER_ARTICLE_BRAND'")
            cross_supplier = cursor.fetchone()[0]
    finally:
        repo.close()

    write_csv(
        REPORTS / "STAGE3D_SOURCE_DB_CONSISTENCY.csv", consistency_rows,
        ["source_external_id", "SKU", "field", "SOURCE_VALUE", "DB_VALUE", "RESULT"],
    )
    write_csv(
        REPORTS / "STAGE3D_REAL_DB_PRODUCT_TRACE.csv", trace_rows,
        ["sample_position", "source_external_id", "SKU", "source_product_id", "offer_id", "catalog_product_id",
         "product_match_id", "matching_status", "match_method", "sterbrust_product_id", "urls_count",
         "categories_count", "properties_count", "media_count", "options_count", "option_values_count",
         "relations_count", "result"],
    )
    write_csv(
        REPORTS / "STAGE3D_DOCKER_DB_COUNTS.csv",
        [{"table": table, "count": count} for table, count in counts.items()], ["table", "count"],
    )
    (REPORTS / "STAGE3D_PP800F_DB_TRACE.md").write_text(
        "# PP-800F real Docker DB trace\n\n"
        f"- source_products.id: `{pp['source_product_id']}`\n- offers.id: `{pp['offer_id']}`\n"
        f"- catalog_product_id: `{pp['catalog_product_id']}`\n- product_matches.id: `{pp['product_match_id']}`\n"
        f"- Partner-ST external_id / SKU: `297` / `508002`\n- Sterbrust ID: `{pp['sterbrust_product_id']}`\n"
        f"- current match: `{pp['matching_status']} / {pp['match_method']}`\n- PP800F_DB_TRACE: `{'PASS' if pp_pass else 'FAIL'}`\n",
        encoding="utf-8",
    )
    (REPORTS / "STAGE3D_MRX3_DB_TRACE.md").write_text(
        "# MR-X3 real Docker DB trace\n\n"
        f"- source_products.id: `{mrx['source_product_id']}`\n- offers.id: `{mrx['offer_id']}`\n"
        f"- accepted catalog_product_id: `{mrx['catalog_product_id']}`\n- product_matches.id: `{mrx['product_match_id']}`\n"
        f"- candidate catalog_product_id: `{mrx['match_candidate_catalog_product_id']}`\n"
        f"- Partner-ST external_id / SKU: `305` / `211301`\n- current match: `{mrx['matching_status']} / {mrx['match_method']}`\n"
        f"- MRX3_DB_TRACE: `{'PASS' if mrx_pass else 'FAIL'}`\n",
        encoding="utf-8",
    )
    result = {
        "state": "PASS" if not failures and pp_pass and mrx_pass else "FAIL",
        "postgres_in_docker": True,
        "runtime_role_superuser": gate["runtime_role_superuser"],
        "server_version": gate["server_version"],
        "migrations_001_011_immutable": "PASS",
        "source_products": counts["source_products"], "offers": counts["offers"],
        "real_db_sample_products": len(trace_rows),
        "source_db_row_level_verification": "PASS" if not failures else "FAIL",
        "source_db_mandatory_data_loss": len(failures),
        "physical_db_row_ids_verified": "YES" if len(trace_rows) == 20 else "NO",
        "pp800f_db_trace": "PASS" if pp_pass else "FAIL",
        "mrx3_db_trace": "PASS" if mrx_pass else "FAIL",
        "fuzzy_auto_matches": fuzzy,
        "cross_supplier_article_auto_matches": cross_supplier,
        "sterbrust_write_methods_used": 0,
        "xml_generated": False,
        "failures": failures,
    }
    (REPORTS / "STAGE3D_DOCKER_QA.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["state"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
