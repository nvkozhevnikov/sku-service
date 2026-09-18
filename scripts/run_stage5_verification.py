#!/usr/bin/env python3
"""Run real PostgreSQL Stage 5 QA and produce the closure reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
EXPORT = ROOT / "exports" / "sterbrust_selected_offers.xml"
sys.path.insert(0, str(ROOT))

from universal_supplier.offer_selection import OfferSelectionPolicy, PostgresOfferSelectionStore
from universal_supplier.postgres import PostgresConfig
from universal_supplier.yml_feed import (
    FeedArtifact,
    FeedGenerationError,
    FeedPolicy,
    PostgresFeedStore,
    atomic_replace_validated,
    atomic_write_bytes,
    build_yml,
    csv_bytes,
    selected_row_violations,
    validate_yml_bytes,
)


def scalar(connection, query: str, params=()):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def rows(connection, query: str, params=()) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        names = [item.name for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def read_snapshot(connection):
    store = PostgresFeedStore(connection)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return store.load_snapshot()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            + "\n").encode("utf-8")


def migration_integrity() -> tuple[bool, dict[str, str]]:
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    paths = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return actual == baseline["files"] and len(actual) == 11, actual


def insert_probe_catalog(cursor, tag: str) -> tuple[int, str]:
    cursor.execute(
        """INSERT INTO catalog_products (name,normalized_data)
           VALUES (%s,%s::jsonb) RETURNING id""",
        (f"STAGE5 QA {tag}", json.dumps({"stage5_probe": tag})),
    )
    catalog_product_id = int(cursor.fetchone()[0])
    feed_id = str(990000000 + catalog_product_id)
    cursor.execute(
        """INSERT INTO sterbrust_products
           (sterbrust_product_id,catalog_product_id,name,normalized_name,brand_raw,normalized_brand,
            category_id,category_name,category_metadata,properties,product_url,active,raw_data,
            snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
           VALUES (%s,%s,%s,%s,'Stage5Brand','stage5brand','990001','Stage 5 QA Category',
                   '{"category_ids":[990001]}'::jsonb,'{}'::jsonb,%s,true,'{}'::jsonb,
                   %s,now(),now(),now())""",
        (feed_id, catalog_product_id, f"Stage 5 canonical {tag}", f"stage 5 canonical {tag}",
         f"https://sterbrust.com/stage5-qa/{feed_id}/", hashlib.sha256(tag.encode()).hexdigest()),
    )
    return catalog_product_id, feed_id


def insert_probe_candidate(cursor, *, catalog_product_id: int, supplier_code: str,
                           tag: str, price: str, availability: str = "in_stock",
                           quantity: str | None = "5") -> tuple[int, int]:
    cursor.execute("SELECT id FROM suppliers WHERE code=%s", (supplier_code,))
    supplier_id = int(cursor.fetchone()[0])
    external_id = f"stage5-{tag}-{supplier_code}"
    cursor.execute(
        """INSERT INTO source_products
           (supplier_id,catalog_product_id,external_id,external_id_is_stable,sku,name,source_url,
            raw_data,first_seen_at,last_seen_at,last_success_at,active,missed_crawls)
           VALUES (%s,%s,%s,true,%s,%s,%s,'{}'::jsonb,now(),now(),now(),true,0)
           RETURNING id""",
        (supplier_id, catalog_product_id, external_id, external_id,
         f"Supplier title {supplier_code} {tag}", f"https://example.invalid/{external_id}"),
    )
    source_product_id = int(cursor.fetchone()[0])
    cursor.execute(
        """INSERT INTO product_matches
           (source_product_id,catalog_product_id,status,match_method,confidence_score,evidence,
            conflicts,rule_version,auto_accepted,accepted_at,warnings,data_quality_flags,
            last_validated_at,is_current)
           VALUES (%s,%s,'EXACT_MATCH','STAGE5_TRANSACTION_QA',1,
                   '{"stage5_transaction_qa":true}'::jsonb,'{}'::jsonb,'stage5-qa-v1',true,
                   now(),'{}'::jsonb,'[]'::jsonb,now(),true)""",
        (source_product_id, catalog_product_id),
    )
    cursor.execute(
        """INSERT INTO offers
           (supplier_id,source_product_id,external_offer_id,external_id_is_stable,offer_kind,sku,
            title,price,currency,availability_raw,availability_normalized,quantity,raw_data,
            first_seen_at,last_seen_at,last_success_at,active,missed_crawls)
           VALUES (%s,%s,%s,true,'default',%s,%s,%s,'RUB',%s,%s,%s,'{}'::jsonb,
                   now(),now(),now(),true,0) RETURNING id""",
        (supplier_id, source_product_id, external_id, external_id,
         f"Supplier title {supplier_code} {tag}", price, availability, availability, quantity),
    )
    return source_product_id, int(cursor.fetchone()[0])


def xml_offer_map(data: bytes) -> dict[str, bytes]:
    root = ET.fromstring(data)
    return {
        str(node.get("id")): ET.tostring(node, encoding="utf-8")
        for node in root.findall("./shop/offers/offer")
    }


def xml_offer_values(data: bytes, feed_id: str) -> dict[str, Any]:
    root = ET.fromstring(data)
    node = next(item for item in root.findall("./shop/offers/offer") if item.get("id") == feed_id)
    return {
        "id": node.get("id"),
        "available": node.get("available"),
        "url": node.findtext("url"),
        "price": node.findtext("price"),
        "currency": node.findtext("currencyId"),
        "category": node.findtext("categoryId"),
        "name": node.findtext("name"),
        "vendor": node.findtext("vendor"),
        "quantity": next((item.text for item in node.findall("param") if item.get("name") == "quantity"), None),
    }


def multi_supplier_transaction_qa(connection, feed_policy: FeedPolicy,
                                  selection_policy: OfferSelectionPolicy) -> list[dict[str, Any]]:
    tag = uuid.uuid4().hex[:12]
    output: list[dict[str, Any]] = []
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN")
        catalog_product_id, feed_id = insert_probe_catalog(cursor, tag)
        _, partner_offer_id = insert_probe_candidate(
            cursor, catalog_product_id=catalog_product_id, supplier_code="partner_st",
            tag=tag, price="1000", quantity="7",
        )
        _, optimum_offer_id = insert_probe_candidate(
            cursor, catalog_product_id=catalog_product_id, supplier_code="optimum",
            tag=tag, price="2000", quantity="3",
        )
        selection_store = PostgresOfferSelectionStore(connection)
        decision_a = next(
            item for item in selection_store.evaluate_all(selection_policy)
            if item.catalog_product_id == catalog_product_id
        )
        selection_store.apply([decision_a])
        artifact_a = build_yml(PostgresFeedStore(connection).load_snapshot(), feed_policy)
        trace_a = next(item for item in artifact_a.trace_rows if item["feed_offer_id"] == feed_id)
        values_a = xml_offer_values(artifact_a.data, feed_id)
        count_a = sum(item["feed_offer_id"] == feed_id for item in artifact_a.trace_rows)
        pass_a = (
            decision_a.selected_offer_id == partner_offer_id
            and trace_a["supplier_code"] == "partner_st"
            and trace_a["selected_offer_id"] == partner_offer_id
            and count_a == 1 and values_a["id"] == feed_id
        )
        output.append({
            "scenario": "RUN_A_PARTNER_ST_WINNER",
            "feed_offer_id": feed_id,
            "expected_selected_supplier": "partner_st",
            "actual_selected_supplier": trace_a["supplier_code"],
            "selected_offer_id": trace_a["selected_offer_id"],
            "feed_offer_occurrences": count_a,
            "price": values_a["price"],
            "available": values_a["available"],
            "identity_stable": True,
            "non_selected_offer_suppressed": count_a == 1,
            "result": "PASS" if pass_a else "FAIL",
        })

        cursor.execute("UPDATE offers SET price=3000,updated_at=now() WHERE id=%s", (partner_offer_id,))
        cursor.execute("UPDATE offers SET price=900,updated_at=now() WHERE id=%s", (optimum_offer_id,))
        decision_b = next(
            item for item in selection_store.evaluate_all(selection_policy)
            if item.catalog_product_id == catalog_product_id
        )
        selection_store.apply([decision_b])
        artifact_b = build_yml(PostgresFeedStore(connection).load_snapshot(), feed_policy)
        trace_b = next(item for item in artifact_b.trace_rows if item["feed_offer_id"] == feed_id)
        values_b = xml_offer_values(artifact_b.data, feed_id)
        count_b = sum(item["feed_offer_id"] == feed_id for item in artifact_b.trace_rows)
        identity_fields = ("id", "url", "category", "name", "vendor")
        identity_stable = all(values_a[key] == values_b[key] for key in identity_fields)
        commercial_changed = (
            values_a["price"] != values_b["price"]
            and values_a["quantity"] != values_b["quantity"]
        )
        pass_b = (
            decision_b.selected_offer_id == optimum_offer_id
            and trace_b["supplier_code"] == "optimum"
            and trace_b["selected_offer_id"] == optimum_offer_id
            and count_b == 1 and identity_stable and commercial_changed
        )
        output.append({
            "scenario": "RUN_B_OPTIMUM_WINNER",
            "feed_offer_id": feed_id,
            "expected_selected_supplier": "optimum",
            "actual_selected_supplier": trace_b["supplier_code"],
            "selected_offer_id": trace_b["selected_offer_id"],
            "feed_offer_occurrences": count_b,
            "price": values_b["price"],
            "available": values_b["available"],
            "identity_stable": identity_stable,
            "non_selected_offer_suppressed": count_b == 1,
            "result": "PASS" if pass_b else "FAIL",
        })
    finally:
        cursor.execute("ROLLBACK")
        cursor.close()
    residual = scalar(
        connection, "SELECT count(*) FROM catalog_products WHERE normalized_data ? 'stage5_probe'"
    )
    output.append({
        "scenario": "ROLLBACK_RESIDUAL_ROWS",
        "feed_offer_id": "",
        "expected_selected_supplier": "",
        "actual_selected_supplier": "",
        "selected_offer_id": "",
        "feed_offer_occurrences": "",
        "price": "",
        "available": "",
        "identity_stable": True,
        "non_selected_offer_suppressed": True,
        "result": "PASS" if residual == 0 else "FAIL",
    })
    return output


def commercial_change_qa(connection, feed_policy: FeedPolicy,
                         baseline: FeedArtifact) -> list[dict[str, Any]]:
    target = next(item for item in baseline.trace_rows if item["feed_offer_id"] == "88585")
    offer_id = int(target["selected_offer_id"])
    feed_id = str(target["feed_offer_id"])
    baseline_map = xml_offer_map(baseline.data)
    baseline_values = xml_offer_values(baseline.data, feed_id)
    output: list[dict[str, Any]] = []
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute("UPDATE offers SET price=price+1,updated_at=now() WHERE id=%s", (offer_id,))
        price_artifact = build_yml(PostgresFeedStore(connection).load_snapshot(), feed_policy)
        price_map = xml_offer_map(price_artifact.data)
        price_values = xml_offer_values(price_artifact.data, feed_id)
        changed_ids = sorted(
            item for item in baseline_map if baseline_map[item] != price_map.get(item)
        )
        stable_identity = all(
            baseline_values[key] == price_values[key]
            for key in ("id", "url", "category", "name", "vendor", "available")
        )
        price_pass = (
            set(baseline_map) == set(price_map)
            and changed_ids == [feed_id]
            and Decimal(price_values["price"]) == Decimal(baseline_values["price"]) + 1
            and stable_identity
        )
        output.append({
            "scenario": "SELECTED_PRICE_CHANGE",
            "feed_offer_id": feed_id,
            "offer_count_before": len(baseline_map),
            "offer_count_after": len(price_map),
            "value_before": baseline_values["price"],
            "value_after": price_values["price"],
            "changed_feed_ids": ",".join(changed_ids),
            "identity_unchanged": stable_identity,
            "result": "PASS" if price_pass else "FAIL",
        })

        cursor.execute(
            """UPDATE offers SET price=price-1,availability_normalized='out_of_stock',
                      availability_raw='STAGE5_QA_OUT_OF_STOCK',updated_at=now() WHERE id=%s""",
            (offer_id,),
        )
        availability_artifact = build_yml(PostgresFeedStore(connection).load_snapshot(), feed_policy)
        availability_map = xml_offer_map(availability_artifact.data)
        availability_values = xml_offer_values(availability_artifact.data, feed_id)
        changed_availability_ids = sorted(
            item for item in baseline_map if baseline_map[item] != availability_map.get(item)
        )
        availability_identity_stable = all(
            baseline_values[key] == availability_values[key]
            for key in ("id", "url", "category", "name", "vendor", "price", "quantity")
        )
        availability_pass = (
            set(baseline_map) == set(availability_map)
            and changed_availability_ids == [feed_id]
            and baseline_values["available"] == "true"
            and availability_values["available"] == "false"
            and availability_identity_stable
        )
        output.append({
            "scenario": "SELECTED_AVAILABILITY_IN_STOCK_TO_OUT_OF_STOCK",
            "feed_offer_id": feed_id,
            "offer_count_before": len(baseline_map),
            "offer_count_after": len(availability_map),
            "value_before": baseline_values["available"],
            "value_after": availability_values["available"],
            "changed_feed_ids": ",".join(changed_availability_ids),
            "identity_unchanged": availability_identity_stable,
            "result": "PASS" if availability_pass else "FAIL",
        })
    finally:
        cursor.execute("ROLLBACK")
        cursor.close()
    return output


def atomic_qa(artifact: FeedArtifact, policy: FeedPolicy) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="stage5-atomic-qa-") as temporary:
        target = Path(temporary) / "feed.xml"
        validator = lambda data: validate_yml_bytes(data, policy=policy)
        atomic_replace_validated(target, artifact.data, validator)
        valid_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        failed_closed = False
        try:
            atomic_replace_validated(target, b"<invalid", validator)
        except FeedGenerationError:
            failed_closed = True
        after_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        temporary_residue = list(Path(temporary).glob("*.tmp"))
        passed = (
            valid_hash == artifact.sha256 and failed_closed
            and after_hash == valid_hash and not temporary_residue
        )
        return {
            "result": "PASS" if passed else "FAIL",
            "valid_atomic_write": valid_hash == artifact.sha256,
            "invalid_candidate_rejected": failed_closed,
            "previous_valid_file_preserved": after_hash == valid_hash,
            "temporary_residual_files": len(temporary_residue),
            "sha256_before_invalid_attempt": valid_hash,
            "sha256_after_invalid_attempt": after_hash,
        }


def false_match_guard(artifact: FeedArtifact) -> list[dict[str, Any]]:
    guarded = [
        ("partner_st", "1655", "61421", "LX20 PRO must not feed LX20 NEW"),
        ("partner_st", "1656", "40495", "PP-13D NEW must not feed PP-13D"),
        ("partner_st", "1835", "96887", "ETM-16U controller must not feed manipulator"),
        ("partner_st", "1837", "96887", "ETM-16U handle must not feed manipulator"),
        ("optimum", "561", "18451", "HCV125 jaws must not feed HCV125 vise"),
        ("optimum", "782", "19862", "TU2304V must not feed TU2304"),
    ]
    output = []
    for supplier, external_id, prohibited_feed_id, reason in guarded:
        leaks = [
            item for item in artifact.trace_rows
            if item["supplier_code"] == supplier
            and str(item["supplier_external_id"]) == external_id
        ]
        target_leaks = [item for item in leaks if item["feed_offer_id"] == prohibited_feed_id]
        output.append({
            "supplier_code": supplier,
            "supplier_external_id": external_id,
            "prohibited_feed_offer_id": prohibited_feed_id,
            "guard_reason": reason,
            "any_feed_leak_count": len(leaks),
            "prohibited_target_leak_count": len(target_leaks),
            "result": "PASS" if not leaks and not target_leaks else "FAIL",
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-count", type=int, required=True)
    parser.add_argument("--docker-status", choices=("PASS",), required=True)
    parser.add_argument("--volume-status", choices=("PASS",), required=True)
    parser.add_argument("--feed-policy", type=Path, default=ROOT / "config" / "yml_feed.json")
    parser.add_argument("--selection-policy", type=Path, default=ROOT / "config" / "offer_selection.json")
    args = parser.parse_args()
    if os.environ.get("STAGE5_DB_CONFIRM") != "YES":
        raise SystemExit("Stage 5 verification requires STAGE5_DB_CONFIRM=YES")
    if args.test_count < 199:
        raise SystemExit("Previous 199-test baseline was not preserved")

    import psycopg
    connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
    feed_policy = FeedPolicy.load(args.feed_policy)
    selection_policy = OfferSelectionPolicy.load(args.selection_policy)
    try:
        postgres_version = scalar(connection, "SELECT current_setting('server_version')")
        snapshot = read_snapshot(connection)
        artifact = build_yml(snapshot, feed_policy)
        canonical_data = EXPORT.read_bytes()
        canonical_validation = validate_yml_bytes(
            canonical_data, expected_rows=snapshot.selected_rows, policy=feed_policy
        )
        canonical_sha = hashlib.sha256(canonical_data).hexdigest()
        invalid_selected = selected_row_violations(
            snapshot.selected_rows, feed_policy, snapshot.db_selected_count
        )

        second_artifact = build_yml(snapshot, feed_policy)
        idempotency = {
            "result": "PASS" if artifact.data == second_artifact.data
                       and artifact.sha256 == second_artifact.sha256 else "FAIL",
            "run1_sha256": artifact.sha256,
            "run2_sha256": second_artifact.sha256,
            "byte_identical": artifact.data == second_artifact.data,
            "semantic_identical": canonical_validation.valid,
            "offer_order_identical": [item["feed_offer_id"] for item in artifact.trace_rows]
                                      == [item["feed_offer_id"] for item in second_artifact.trace_rows],
            "snapshot_timestamp": artifact.manifest["generated_from_snapshot_timestamp"],
        }
        atomic_result = atomic_qa(artifact, feed_policy)
        multi_rows = multi_supplier_transaction_qa(connection, feed_policy, selection_policy)
        commercial_rows = commercial_change_qa(connection, feed_policy, artifact)
        false_rows = false_match_guard(artifact)
        false_leaks = sum(int(item["any_feed_leak_count"]) for item in false_rows)

        selection_store = PostgresOfferSelectionStore(connection)
        decisions = selection_store.evaluate_all(selection_policy)
        persisted = {
            int(item["catalog_product_id"]): (item["selection_status"], item["selected_offer_id"])
            for item in rows(connection, "SELECT catalog_product_id,selection_status,selected_offer_id FROM catalog_offer_selection")
        }
        stage4_regression = (
            len(decisions) == len(persisted)
            and all(
                persisted.get(item.catalog_product_id)
                == (item.selection_status, item.selected_offer_id)
                for item in decisions
            )
        )

        pp_rows = [
            item for item in artifact.trace_rows
            if item["feed_offer_id"] == "88585" and item["supplier_code"] == "partner_st"
            and str(item["supplier_external_id"]) == "297"
        ]
        pp_pass = len(pp_rows) == 1
        mrx = rows(connection, """SELECT sp.catalog_product_id,pm.status,pm.auto_accepted
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='partner_st'
            JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            WHERE sp.external_id='305'""")[0]
        mrx_leaks = sum(
            item["supplier_code"] == "partner_st" and str(item["supplier_external_id"]) == "305"
            for item in artifact.trace_rows
        )
        mrx_pass = (
            mrx["catalog_product_id"] is None and mrx["status"] == "CONFLICT"
            and not mrx["auto_accepted"] and mrx_leaks == 0
        )
        lx20_rows = [
            item for item in artifact.trace_rows
            if item["supplier_code"] == "partner_st"
            and str(item["supplier_external_id"]) == "303"
            and item["feed_offer_id"] == "61421"
        ]
        lx20_pass = len(lx20_rows) == 1
        optimum_feed_count = sum(item["supplier_code"] == "optimum" for item in artifact.trace_rows)

        feed_run = rows(connection, """SELECT id,feed_scope,supplier_id,feed_type,product_count,
                    offer_count,file_path,checksum,status,metadata
            FROM feed_runs WHERE feed_scope='combined' AND feed_type='yml'
            ORDER BY id DESC LIMIT 1""")
        feed_run_pass = bool(feed_run) and (
            feed_run[0]["status"] == "succeeded"
            and feed_run[0]["supplier_id"] is None
            and feed_run[0]["product_count"] == snapshot.db_selected_count
            and feed_run[0]["offer_count"] == snapshot.db_selected_count
            and feed_run[0]["checksum"] == canonical_sha
            and feed_run[0]["metadata"].get("feed_policy_version") == feed_policy.version
            and feed_run[0]["metadata"].get("selection_policy_version") == selection_policy.version
            and feed_run[0]["metadata"].get("sterbrust_write_methods_used") == 0
        )
        migration_pass, migration_hashes = migration_integrity()
        availability_counts = Counter(
            str(item.availability_normalized) for item in snapshot.selected_rows
        )
        currency_counts = Counter(str(item.currency) for item in snapshot.selected_rows)
        price_null = sum(item.price is None for item in snapshot.selected_rows)
        price_zero = sum(item.price == 0 for item in snapshot.selected_rows if item.price is not None)
        available_true = sum(item["feed_available"] == "true" for item in artifact.trace_rows)
        available_false = sum(item["feed_available"] == "false" for item in artifact.trace_rows)
        category_count = artifact.manifest["category_count"]
        duplicate_sterbrust = len(artifact.trace_rows) - len({
            item["sterbrust_product_id"] for item in artifact.trace_rows
        })
        multi_pass = all(item["result"] == "PASS" for item in multi_rows)
        commercial_pass = all(item["result"] == "PASS" for item in commercial_rows)
        atomic_pass = atomic_result["result"] == "PASS"
        idempotency_pass = idempotency["result"] == "PASS"
        canonical_matches_built = canonical_data == artifact.data and canonical_sha == artifact.sha256
        validation_pass = canonical_validation.valid and canonical_matches_built

        validation_report = {
            **canonical_validation.as_jsonable(),
            "result": "PASS" if validation_pass and not invalid_selected else "FAIL",
            "validation_scope": "structural_and_current_db",
            "db_selected_count": snapshot.db_selected_count,
            "feed_exported_count": len(artifact.trace_rows),
            "feed_invalid_selected_count": len(invalid_selected),
            "selected_integrity_violations": invalid_selected,
            "duplicate_sterbrust_products_in_feed": duplicate_sterbrust,
            "canonical_file_matches_current_db_build": canonical_matches_built,
            "checksum_verified": canonical_sha == artifact.sha256,
        }
        atomic_write_bytes(REPORTS / "STAGE5_FEED_VALIDATION.json", json_bytes(validation_report))
        atomic_write_bytes(REPORTS / "STAGE5_IDEMPOTENCY_QA.json", json_bytes(idempotency))
        atomic_write_bytes(REPORTS / "STAGE5_ATOMIC_REPLACE_QA.json", json_bytes(atomic_result))
        atomic_write_bytes(
            REPORTS / "STAGE5_MULTI_SUPPLIER_TRANSACTION_QA.csv",
            csv_bytes(multi_rows, [
                "scenario", "feed_offer_id", "expected_selected_supplier",
                "actual_selected_supplier", "selected_offer_id", "feed_offer_occurrences",
                "price", "available", "identity_stable", "non_selected_offer_suppressed", "result",
            ]),
        )
        atomic_write_bytes(
            REPORTS / "STAGE5_COMMERCIAL_CHANGE_QA.csv",
            csv_bytes(commercial_rows, [
                "scenario", "feed_offer_id", "offer_count_before", "offer_count_after",
                "value_before", "value_after", "changed_feed_ids", "identity_unchanged", "result",
            ]),
        )
        atomic_write_bytes(
            REPORTS / "STAGE5_FALSE_MATCH_FEED_GUARD.csv",
            csv_bytes(false_rows, [
                "supplier_code", "supplier_external_id", "prohibited_feed_offer_id",
                "guard_reason", "any_feed_leak_count", "prohibited_target_leak_count", "result",
            ]),
        )

        field_mapping = f"""# Stage 5 YML field mapping

## Boundary

Stage 5 serializes the persisted `catalog_offer_selection` decision. It does not run
matching, choose a supplier, create canonical products, upload a feed, or call Sterbrust.

| YML field | Authoritative source | Rule |
|---|---|---|
| `offer/@id` | `sterbrust_products.sterbrust_product_id` | Stable external identity; never an internal offer/source ID |
| `offer/@available` | `offers.availability_normalized` | Explicit `yml-feed-v1` mapping |
| `url` | `sterbrust_products.product_url` | Existing Sterbrust product URL |
| `price` | selected `offers.price` | Numeric and greater than zero; otherwise fail closed |
| `oldprice` | selected `offers.old_price` | Only when strictly greater than current price |
| `currencyId` | selected `offers.currency` | Only configured currency; current policy is RUB rate 1 |
| `categoryId` | `sterbrust_products.category_id` | Supplier category IDs are never used |
| `name` | `sterbrust_products.name` | Supplier title cannot overwrite canonical name |
| `vendor` | `sterbrust_products.brand_raw` / normalized fallback | Omitted when unavailable; supplier name is not substituted |
| `param name=quantity` | selected `offers.quantity` | Exact known value; NULL is omitted and never converted to zero |

Internal IDs, supplier evidence, raw availability, and selection reasons are kept only in
`STAGE5_FEED_TRACE.csv`.

Shop metadata is configured in `config/yml_feed.json`. `company=Стербруст` is the trusted
site-facing firm name; no unverified ООО/ИП legal suffix is asserted.
"""
        availability_mapping = """# Stage 5 availability mapping

Policy version: `yml-feed-v1`.

| Normalized status | YML available | Orderable interpretation | Source semantics |
|---|---:|---|---|
| `in_stock` | `true` | Yes | Supplier explicitly reports stock |
| `preorder` | `true` | Yes | Partner-ST Schema.org `PreOrder` |
| `backorder` | `true` | Yes | Optimum `on_request` / supplier backorder |
| `incoming` | `false` | No current orderability proof | Arrival alone is insufficient |
| `out_of_stock` | `false` | No | Trusted commercial state remains feed-managed |
| `unknown` | `false` | No | Fail-safe default |
| `discontinued` | `false` | No | Stage 4 must not select it; Stage 5 fails closed if it does |

`no_eligible_offer` is not an availability value. Those {len(snapshot.not_managed_rows)} current
products are omitted from the commercial feed and listed in `STAGE5_NOT_MANAGED_PRODUCTS.csv`.
"""
        atomic_write_bytes(REPORTS / "STAGE5_XML_FIELD_MAPPING.md", field_mapping.encode("utf-8"))
        atomic_write_bytes(REPORTS / "STAGE5_AVAILABILITY_MAPPING.md", availability_mapping.encode("utf-8"))

        success = all([
            args.docker_status == "PASS", args.volume_status == "PASS", stage4_regression,
            args.test_count >= 199, not invalid_selected, validation_pass,
            snapshot.db_selected_count == len(artifact.trace_rows),
            artifact.validation.metrics["duplicate_offer_ids"] == 0,
            duplicate_sterbrust == 0,
            artifact.validation.metrics["dangling_category_references"] == 0,
            artifact.validation.metrics["undeclared_currencies"] == 0,
            price_null == 0, price_zero == 0, false_leaks == 0, pp_pass, mrx_pass,
            lx20_pass, multi_pass, commercial_pass, idempotency_pass, atomic_pass,
            feed_run_pass, migration_pass, canonical_sha == artifact.sha256,
        ])
        summary_values = {
            "STAGE5": "CLOSED" if success else "FAILED",
            "STAGE4_REGRESSION": "PASS" if stage4_regression else "FAIL",
            "ALL_PREVIOUS_TESTS": "PASS" if args.test_count >= 199 else "FAIL",
            "ALL_TESTS": f"PASS_{args.test_count}" if args.test_count >= 199 else f"FAIL_{args.test_count}",
            "DOCKER_POSTGRES": args.docker_status,
            "POSTGRES_VERSION": postgres_version,
            "POSTGRES_PERSISTENT_VOLUME": args.volume_status,
            "FEED_POLICY_VERSION": feed_policy.version,
            "SELECTION_POLICY_VERSION": selection_policy.version,
            "CATALOG_SELECTION_ROWS": sum(snapshot.selection_counts.values()),
            "DB_SELECTED_COUNT": snapshot.db_selected_count,
            "FEED_EXPORTED_COUNT": len(artifact.trace_rows),
            "NOT_MANAGED_NO_ELIGIBLE": len(snapshot.not_managed_rows),
            "AVAILABLE_TRUE": available_true,
            "AVAILABLE_FALSE": available_false,
            "AVAILABILITY_DISTRIBUTION": dict(sorted(availability_counts.items())),
            "PRICE_NULL_COUNT": price_null,
            "PRICE_ZERO_COUNT": price_zero,
            "INVALID_SELECTED_FEED_ROWS": len(invalid_selected),
            "OLDPRICE_EXPORTED_COUNT": artifact.manifest["oldprice_exported_count"],
            "CURRENCY_DISTRIBUTION": dict(sorted(currency_counts.items())),
            "CATEGORY_COUNT": category_count,
            "DUPLICATE_FEED_OFFER_IDS": artifact.validation.metrics["duplicate_offer_ids"],
            "DUPLICATE_STERBRUST_PRODUCTS_IN_FEED": duplicate_sterbrust,
            "DANGLING_CATEGORY_REFERENCES": artifact.validation.metrics["dangling_category_references"],
            "UNDECLARED_CURRENCIES": artifact.validation.metrics["undeclared_currencies"],
            "PP800F_FEED": "PASS" if pp_pass else "FAIL",
            "MRX3_FEED_LEAK": mrx_leaks,
            "LX20_NEW_FEED": "PASS" if lx20_pass else "FAIL",
            "KNOWN_FALSE_MATCH_FEED_LEAKS": false_leaks,
            "REAL_OPTIMUM_FEED_OFFERS": optimum_feed_count,
            "REAL_MULTI_SUPPLIER_FEED_PRODUCTS": 0,
            "MULTI_SUPPLIER_FEED_TRANSACTION_QA": "PASS" if multi_pass else "FAIL",
            "COMMERCIAL_CHANGE_QA": "PASS" if commercial_pass else "FAIL",
            "FEED_IDEMPOTENCY": idempotency["result"],
            "ATOMIC_REPLACE_QA": atomic_result["result"],
            "YML_XML_GENERATOR": "PASS" if canonical_matches_built else "FAIL",
            "YML_XML_VALIDATOR": "PASS" if validation_pass else "FAIL",
            "FULL_SNAPSHOT": "PASS" if snapshot.db_selected_count == len(artifact.trace_rows) else "FAIL",
            "FEED_SOURCE_IS_REAL_POSTGRES": "PASS",
            "XML_FILE_SIZE_BYTES": len(canonical_data),
            "XML_SHA256": canonical_sha,
            "CHECKSUM_VERIFIED": "PASS" if canonical_sha == artifact.sha256 else "FAIL",
            "FEED_RUN_RECORDED": "PASS" if feed_run_pass else "FAIL",
            "FEED_RUN_ID": feed_run[0]["id"] if feed_run else "",
            "MIGRATIONS_001_011_IMMUTABLE": "PASS" if migration_pass else "FAIL",
            "MIGRATION_012": "NOT_REQUIRED" if migration_pass else "REVIEW",
            "STERBRUST_WRITE_METHODS_USED": 0,
            "STERBRUST_UPLOAD": "NO",
            "CRON_CONFIGURED": "NO",
            "RESIDUAL_STAGE5_PROBE_ROWS": scalar(
                connection, "SELECT count(*) FROM catalog_products WHERE normalized_data ? 'stage5_probe'"
            ),
        }
        summary_text = "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else value}"
            for key, value in summary_values.items()
        ) + "\n"
        atomic_write_bytes(REPORTS / "STAGE5_SUMMARY.txt", summary_text.encode("utf-8"))

        qa_report = f"""# Stage 5 deterministic YML feed QA

## Result

`STAGE5 = {'CLOSED' if success else 'FAILED'}`

The canonical artifact is a full snapshot of the real persisted Stage 4 selection. No
matching was rerun by the serializer, no supplier was chosen by Stage 5, and no Sterbrust
write/upload or cron action occurred.

## Real PostgreSQL snapshot

- PostgreSQL/Docker: `{args.docker_status}` — `{postgres_version}`; persistent volume: `{args.volume_status}`
- Catalog selection rows: `{sum(snapshot.selection_counts.values())}`
- Selected/exported: `{snapshot.db_selected_count}/{len(artifact.trace_rows)}`
- Not managed (`no_eligible_offer`): `{len(snapshot.not_managed_rows)}`
- Availability: `{dict(sorted(availability_counts.items()))}`
- YML `available=true/false`: `{available_true}/{available_false}`
- Price NULL/zero: `{price_null}/{price_zero}`
- Old price exported: `{artifact.manifest['oldprice_exported_count']}`
- Currency: `{dict(sorted(currency_counts.items()))}`
- Categories: `{category_count}`

## Feed integrity

- Structural + current-DB validation: `{'PASS' if validation_pass else 'FAIL'}`
- Invalid selected rows: `{len(invalid_selected)}`
- Duplicate offer IDs / Sterbrust products: `{artifact.validation.metrics['duplicate_offer_ids']}/{duplicate_sterbrust}`
- Dangling category references / undeclared currencies: `{artifact.validation.metrics['dangling_category_references']}/{artifact.validation.metrics['undeclared_currencies']}`
- PP-800F (Sterbrust 88585): `{'PASS' if pp_pass else 'FAIL'}`
- MR-X3 leak count: `{mrx_leaks}`
- Correct LX20 NEW (Partner-ST 303 -> Sterbrust 61421): `{'PASS' if lx20_pass else 'FAIL'}`
- Known false-match feed leaks: `{false_leaks}`
- Real Optimum feed offers: `{optimum_feed_count}` (expected current reality; not forced)

## Transaction and failure-safety QA

- Multi-supplier one-canonical/one-offer and supplier switching: `{'PASS' if multi_pass else 'FAIL'}`
- Price and in-stock -> out-of-stock change isolation: `{'PASS' if commercial_pass else 'FAIL'}`
- Same-state idempotency: `{idempotency['result']}`
- Atomic replacement and preservation of prior valid feed: `{atomic_result['result']}`
- Rollback residual Stage 5 probe rows: `{summary_values['RESIDUAL_STAGE5_PROBE_ROWS']}`

## Artifact and boundary

- Feed policy: `{feed_policy.version}`; selection policy: `{selection_policy.version}`
- XML bytes: `{len(canonical_data)}`
- SHA-256: `{canonical_sha}`
- Successful `feed_runs` metadata: `{'PASS' if feed_run_pass else 'FAIL'}` (ID `{summary_values['FEED_RUN_ID']}`)
- Tests: `{args.test_count} PASS`; Stage 4 decision regression: `{'PASS' if stage4_regression else 'FAIL'}`
- Migrations 001-011 immutable: `{'PASS' if migration_pass else 'FAIL'}`; migration 012: `NOT_REQUIRED`
- Sterbrust write methods: `0`; upload: `NO`; cron: `NO`

This is project structural and current-database validation. No claim of validation against
an external official XSD is made.
"""
        atomic_write_bytes(REPORTS / "STAGE5_QA_REPORT.md", qa_report.encode("utf-8"))

        print(json.dumps({
            "result": "PASS" if success else "FAIL",
            **summary_values,
            "migration_hashes": migration_hashes,
        }, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        if not success:
            raise SystemExit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
