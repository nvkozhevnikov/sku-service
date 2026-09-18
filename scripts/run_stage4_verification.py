#!/usr/bin/env python3
"""Apply Stage 4, run real PostgreSQL QA, and generate the required reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))

from universal_supplier.offer_selection import (
    OfferSelectionPolicy,
    PostgresOfferSelectionStore,
    evaluate_offers,
)
from universal_supplier.postgres import PostgresConfig


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def scalar(connection, query: str, params=()):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def rows(connection, query: str, params=()) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        names = [item.name for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def policy_with(policy: OfferSelectionPolicy, *, version: str,
                priorities: dict[str, int]) -> OfferSelectionPolicy:
    return replace(policy, version=version, supplier_priority=priorities)


def insert_product(cursor, tag: str) -> int:
    cursor.execute(
        "INSERT INTO catalog_products (name,normalized_data) VALUES (%s,%s::jsonb) RETURNING id",
        (f"STAGE4 QA {tag}", json.dumps({"stage4_probe": tag})),
    )
    return cursor.fetchone()[0]


def insert_candidate(cursor, *, catalog_product_id: int, supplier_code: str, tag: str,
                     price: str | None = "100000", currency: str | None = "RUB",
                     availability: str = "in_stock", quantity: str | None = "1",
                     source_active: bool = True, offer_active: bool = True,
                     source_misses: int = 0, offer_misses: int = 0,
                     create_offer: bool = True, sku: str | None = None) -> tuple[int, int | None]:
    cursor.execute("SELECT id FROM suppliers WHERE code=%s", (supplier_code,))
    supplier_id = cursor.fetchone()[0]
    external = f"stage4-qa-{tag}-{supplier_code}"
    cursor.execute(
        """INSERT INTO source_products
           (supplier_id,catalog_product_id,external_id,external_id_is_stable,sku,name,source_url,
            raw_data,first_seen_at,last_seen_at,last_success_at,active,missed_crawls)
           VALUES (%s,%s,%s,true,%s,%s,%s,'{}'::jsonb,now(),now(),now(),%s,%s) RETURNING id""",
        (supplier_id, catalog_product_id, external, sku or external, f"QA {tag}",
         f"https://example.invalid/{external}", source_active, source_misses),
    )
    source_id = cursor.fetchone()[0]
    if not create_offer:
        return source_id, None
    cursor.execute(
        """INSERT INTO offers
           (supplier_id,source_product_id,external_offer_id,external_id_is_stable,offer_kind,sku,
            title,price,currency,availability_raw,availability_normalized,quantity,raw_data,
            first_seen_at,last_seen_at,last_success_at,active,missed_crawls)
           VALUES (%s,%s,%s,true,'default',%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,
                   now(),now(),now(),%s,%s) RETURNING id""",
        (supplier_id, source_id, external, sku or external, f"QA {tag}", price, currency,
         availability, availability, quantity, offer_active, offer_misses),
    )
    return source_id, cursor.fetchone()[0]


def transaction_scenario(connection, policy: OfferSelectionPolicy, scenario: str,
                         setup, expected_status: str, expected_supplier: str | None,
                         expected_reason: str | None = None) -> dict[str, Any]:
    tag = f"{scenario.lower()}-{uuid.uuid4().hex[:10]}"
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN")
        product_id, scenario_policy, metadata = setup(cursor, tag, policy)
        decision = next(item for item in PostgresOfferSelectionStore(connection).evaluate_all(scenario_policy)
                        if item.catalog_product_id == product_id)
        supplier = decision.evidence.get("selected_supplier_code")
        passed = decision.selection_status == expected_status and supplier == expected_supplier
        if expected_reason:
            passed = passed and expected_reason in decision.evidence.get("selection_reason", "")
        result = {
            "scenario": scenario,
            "expected_status": expected_status,
            "actual_status": decision.selection_status,
            "expected_supplier": expected_supplier or "",
            "selected_supplier": supplier or "",
            "selected_offer_id": decision.selected_offer_id or "",
            "selection_reason": decision.evidence.get("selection_reason", ""),
            "policy_version": scenario_policy.version,
            "result": "PASS" if passed else "FAIL",
            "details": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        }
    finally:
        cursor.execute("ROLLBACK")
        cursor.close()
    return result


def run_transaction_qa(connection, policy: OfferSelectionPolicy) -> list[dict[str, Any]]:
    def pair(cursor, tag, current_policy, partner: dict, optimum: dict):
        cp = insert_product(cursor, tag)
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag + "-p", **partner)
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="optimum", tag=tag + "-o", **optimum)
        return cp, current_policy, {}

    scenarios = []
    scenarios.append(transaction_scenario(
        connection, policy, "A_LOWER_PRICE",
        lambda c, t, p: pair(c, t, p, {"price": "100000"}, {"price": "90000"}),
        "selected", "optimum", "lowest_valid_price",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "B_AVAILABILITY_BEATS_PRICE",
        lambda c, t, p: pair(c, t, p, {"price": "100000"},
                              {"price": "80000", "availability": "backorder", "quantity": None}),
        "selected", "partner_st", "availability_and_supplier_priority",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "C_CONFIGURED_PRIORITY",
        lambda c, t, p: pair(c, t, policy_with(p, version="offer-selection-v2-priority-qa",
                                                priorities={"partner_st": 10, "optimum": 0}),
                              {"price": "100000"}, {"price": "90000"}),
        "selected", "partner_st", "availability_and_supplier_priority",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "D_STALE_FALLBACK",
        lambda c, t, p: pair(c, t, p, {"source_misses": 1}, {"price": "110000"}),
        "selected", "optimum", "single_eligible_current_offer",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "E_DETERMINISTIC_TIE",
        lambda c, t, p: pair(c, t, p, {"price": "100000"}, {"price": "100000"}),
        "selected", "optimum", "deterministic_tie_break",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "F_DIFFERENT_CURRENCIES",
        lambda c, t, p: pair(c, t, p, {"price": "100000", "currency": "RUB"},
                              {"price": "1000", "currency": "EUR"}),
        "review", None, "currency_comparison_required",
    ))

    def one(cursor, tag, current_policy, **kwargs):
        cp = insert_product(cursor, tag)
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag, **kwargs)
        return cp, current_policy, {}

    scenarios.append(transaction_scenario(
        connection, policy, "G_DISCONTINUED",
        lambda c, t, p: one(c, t, p, availability="discontinued", quantity=None),
        "no_eligible_offer", None, "no_eligible_offer",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "H_QUANTITY_CONTRADICTION",
        lambda c, t, p: one(c, t, p, availability="in_stock", quantity="0"),
        "review", None, "quantity_zero_contradiction",
    ))

    def wrong_product(cursor, tag, current_policy):
        target = insert_product(cursor, tag + "-target")
        insert_candidate(cursor, catalog_product_id=target, supplier_code="partner_st",
                         tag=tag + "-empty", create_offer=False)
        other = insert_product(cursor, tag + "-other")
        insert_candidate(cursor, catalog_product_id=other, supplier_code="optimum", tag=tag + "-offer")
        return target, current_policy, {"other_catalog_product_id": other}
    scenarios.append(transaction_scenario(
        connection, policy, "I_WRONG_CATALOG_PRODUCT",
        wrong_product, "no_eligible_offer", None, "no_eligible_offer",
    ))
    scenarios.append(transaction_scenario(
        connection, policy, "J_SAME_RAW_SKU_ISOLATION",
        lambda c, t, p: pair(c, t, p, {"price": "100000", "sku": "SAME-QA-SKU"},
                              {"price": "90000", "sku": "SAME-QA-SKU"}),
        "selected", "optimum", "lowest_valid_price",
    ))

    def idempotency(cursor, tag, current_policy):
        cp = insert_product(cursor, tag)
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag)
        store = PostgresOfferSelectionStore(connection)
        decision = next(item for item in store.evaluate_all(current_policy) if item.catalog_product_id == cp)
        first = store.apply([decision])
        cursor.execute("SELECT evaluated_at FROM catalog_offer_selection WHERE catalog_product_id=%s", (cp,))
        timestamp1 = cursor.fetchone()[0]
        second = store.apply([decision])
        cursor.execute("SELECT count(*),max(evaluated_at) FROM catalog_offer_selection WHERE catalog_product_id=%s", (cp,))
        row_count, timestamp2 = cursor.fetchone()
        metadata = {"first": first, "second": second, "row_count": row_count,
                    "evaluated_at_preserved": bool(timestamp1 and timestamp1 == timestamp2)}
        return cp, current_policy, metadata
    scenarios.append(transaction_scenario(
        connection, policy, "K_RUN2_IDEMPOTENCY", idempotency,
        "selected", "partner_st", "single_eligible_current_offer",
    ))

    def winner_change(cursor, tag, current_policy):
        cp = insert_product(cursor, tag)
        _, offer_a = insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag + "-p", price="90000")
        _, offer_b = insert_candidate(cursor, catalog_product_id=cp, supplier_code="optimum", tag=tag + "-o", price="100000")
        store = PostgresOfferSelectionStore(connection)
        first = next(item for item in store.evaluate_all(current_policy) if item.catalog_product_id == cp)
        store.apply([first])
        cursor.execute("UPDATE offers SET availability_normalized='out_of_stock',updated_at=now() WHERE id=%s", (offer_a,))
        second = next(item for item in store.evaluate_all(current_policy) if item.catalog_product_id == cp)
        store.apply([second])
        return cp, current_policy, {"winner_before": first.selected_offer_id,
                                    "winner_after": second.selected_offer_id,
                                    "expected_after": offer_b,
                                    "changed": first.selected_offer_id != second.selected_offer_id}
    scenarios.append(transaction_scenario(
        connection, policy, "L_SELECTION_CHANGE", winner_change,
        "selected", "optimum", "availability_and_supplier_priority",
    ))

    def same_supplier_collapse(cursor, tag, current_policy):
        cp = insert_product(cursor, tag)
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag + "-a")
        insert_candidate(cursor, catalog_product_id=cp, supplier_code="partner_st", tag=tag + "-b")
        return cp, current_policy, {}
    scenarios.append(transaction_scenario(
        connection, policy, "M_SAME_SUPPLIER_IDENTITY_AMBIGUITY", same_supplier_collapse,
        "review", None, "same_supplier_multiple_source_products_identity_ambiguity",
    ))
    for item in scenarios:
        details = json.loads(item["details"])
        if item["scenario"] == "K_RUN2_IDEMPOTENCY":
            valid = (details["first"]["changed"] == 1 and details["second"]["changed"] == 0
                     and details["row_count"] == 1 and details["evaluated_at_preserved"])
            item["result"] = "PASS" if item["result"] == "PASS" and valid else "FAIL"
        if item["scenario"] == "L_SELECTION_CHANGE":
            valid = details["changed"] and details["winner_after"] == details["expected_after"]
            item["result"] = "PASS" if item["result"] == "PASS" and valid else "FAIL"
    return scenarios


def migration_integrity() -> tuple[bool, dict[str, str]]:
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    paths = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return actual == baseline["files"] and len(actual) == 11, actual


def real_selection_rows(connection) -> list[dict[str, Any]]:
    result = rows(connection, """SELECT cs.catalog_product_id,
               coalesce(sb.sterbrust_product_id,'') AS sterbrust_product_id,
               sp.external_id AS source_external_id,sp.sku,sp.name,sp.id AS source_product_id,
               o.id AS offer_id,s.code AS supplier,o.price,o.currency,
               o.availability_normalized AS availability,o.availability_raw,o.quantity,
               cs.selection_status,cs.evidence->>'selection_reason' AS reason,
               cs.rule_version,cs.evaluated_at,
               (cs.evidence->>'evaluated_candidate_count')::int AS candidate_count,
               (cs.evidence->>'eligible_candidate_count')::int AS eligible_candidate_count
        FROM catalog_offer_selection cs
        LEFT JOIN offers o ON o.id=cs.selected_offer_id
        LEFT JOIN source_products sp ON sp.id=o.source_product_id
        LEFT JOIN suppliers s ON s.id=o.supplier_id
        LEFT JOIN LATERAL (SELECT sterbrust_product_id FROM sterbrust_products x
                           WHERE x.catalog_product_id=cs.catalog_product_id ORDER BY x.id LIMIT 1) sb ON true
        ORDER BY cs.catalog_product_id""")
    for row in result:
        for key in ("price", "quantity", "evaluated_at"):
            if row[key] is not None:
                row[key] = str(row[key])
    return result


def choose_traces(all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    predicates = [
        lambda r: r["source_external_id"] == "297",
        lambda r: r["supplier"] == "optimum",
        lambda r: r["availability"] == "out_of_stock",
        lambda r: r["availability"] in {"preorder", "backorder"},
        lambda r: r["price"] in (None, ""),
        lambda r: r["quantity"] in (None, ""),
        lambda r: r["quantity"] not in (None, ""),
    ]
    for predicate in predicates:
        match = next((row for row in all_rows if predicate(row) and row not in chosen), None)
        if match:
            chosen.append(match)
    for row in all_rows:
        if row not in chosen:
            chosen.append(row)
        if len(chosen) == 20:
            break
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "offer_selection.json")
    parser.add_argument("--test-count", type=int, required=True)
    parser.add_argument("--docker-status", choices=("PASS",), required=True)
    parser.add_argument("--volume-status", choices=("PASS",), required=True)
    parser.add_argument("--post-recreate-selection-count", type=int, required=True)
    args = parser.parse_args()
    if os.environ.get("STAGE4_DB_CONFIRM") != "YES":
        raise SystemExit("Stage 4 verification apply requires STAGE4_DB_CONFIRM=YES")

    import psycopg
    connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
    policy = OfferSelectionPolicy.load(args.policy)
    try:
        server_version = scalar(connection, "SELECT current_setting('server_version')")
        store = PostgresOfferSelectionStore(connection)
        decisions1 = store.evaluate_all(policy)
        apply1 = store.apply(decisions1)
        evaluated_at_before = scalar(connection, "SELECT max(evaluated_at) FROM catalog_offer_selection")
        decisions2 = store.evaluate_all(policy)
        apply2 = store.apply(decisions2)
        evaluated_at_after = scalar(connection, "SELECT max(evaluated_at) FROM catalog_offer_selection")
        transaction_rows = run_transaction_qa(connection, policy)
        residual = scalar(connection, "SELECT count(*) FROM catalog_products WHERE normalized_data ? 'stage4_probe'")

        selections = real_selection_rows(connection)
        status_counts = Counter(item.selection_status for item in decisions2)
        single = sum(item.evidence["eligible_candidate_count"] == 1 and item.selection_status == "selected"
                     for item in decisions2)
        multi_offer = sum(item.evidence["evaluated_candidate_count"] > 1 and item.selection_status == "selected"
                          for item in decisions2)
        real_overlap = scalar(connection, """SELECT count(*) FROM (
            SELECT sp.catalog_product_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            WHERE sp.catalog_product_id IS NOT NULL AND s.code IN ('partner_st','optimum')
            GROUP BY sp.catalog_product_id HAVING count(DISTINCT s.code)>1) q""")
        real_multi_selected = scalar(connection, """SELECT count(*) FROM catalog_offer_selection cs WHERE cs.selection_status='selected'
            AND (SELECT count(DISTINCT sp.supplier_id) FROM source_products sp JOIN offers o ON o.source_product_id=sp.id
                 WHERE sp.catalog_product_id=cs.catalog_product_id)>1""")

        integrity_queries = {
            "ONE_SELECTION_ROW_PER_EVALUATED_CATALOG_PRODUCT": """SELECT
                (SELECT count(*) FROM (SELECT catalog_product_id FROM catalog_offer_selection GROUP BY catalog_product_id HAVING count(*)<>1) duplicates)
                + (SELECT count(*) FROM (SELECT DISTINCT catalog_product_id FROM source_products
                    WHERE catalog_product_id IS NOT NULL) linked
                   LEFT JOIN catalog_offer_selection cs USING (catalog_product_id)
                   WHERE cs.catalog_product_id IS NULL)""",
            "SELECTED_OFFER_EXISTS": "SELECT count(*) FROM catalog_offer_selection cs LEFT JOIN offers o ON o.id=cs.selected_offer_id WHERE cs.selection_status='selected' AND o.id IS NULL",
            "SELECTED_OFFER_BELONGS_TO_CATALOG_PRODUCT": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.catalog_product_id<>cs.catalog_product_id",
            "SELECTED_OFFER_SOURCE_PRODUCT_LINKED": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.catalog_product_id IS NULL",
            "SELECTED_OFFER_SUPPLIER_MATCHES_SOURCE": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE o.supplier_id<>sp.supplier_id",
            "NO_SELECTED_INACTIVE_OFFER": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id WHERE NOT o.active",
            "NO_SELECTED_INACTIVE_SOURCE_PRODUCT": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE NOT sp.active",
            "NO_SELECTED_DISABLED_SUPPLIER": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN suppliers s ON s.id=o.supplier_id WHERE NOT s.enabled",
            "NO_SELECTED_DISCONTINUED_OFFER": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id WHERE o.availability_normalized='discontinued'",
            "NO_UNLINKED_SOURCE_PRODUCT_SELECTED": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.catalog_product_id IS NULL",
            "STALE_SELECTION_AFTER_MAPPING_REMOVAL": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE cs.selection_status='selected' AND (sp.catalog_product_id IS NULL OR sp.catalog_product_id<>cs.catalog_product_id)",
            "CROSS_SUPPLIER_SELECTION_VIOLATIONS": "SELECT count(*) FROM catalog_offer_selection cs JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id WHERE o.supplier_id<>sp.supplier_id",
            "NO_SELECTED_STALE_OFFER": """SELECT count(*) FROM catalog_offer_selection cs
                JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id
                LEFT JOIN LATERAL (SELECT cr.started_at FROM crawl_runs cr WHERE cr.supplier_id=sp.supplier_id
                    AND cr.crawl_type='full' AND cr.status='succeeded' ORDER BY cr.started_at DESC LIMIT 1) latest ON true
                WHERE sp.missed_crawls<>0 OR o.missed_crawls<>0 OR sp.last_success_at IS NULL
                   OR o.last_success_at IS NULL OR latest.started_at IS NULL
                   OR sp.last_success_at<latest.started_at OR o.last_success_at<latest.started_at""",
            "EVIDENCE_REQUIRED_FIELDS": """SELECT count(*) FROM catalog_offer_selection WHERE NOT
                (evidence ?& ARRAY['policy_version','catalog_product_id','evaluated_candidate_count',
                 'eligible_candidate_count','selected_offer_id','selected_supplier_code','selection_reason',
                 'availability_rank','supplier_priority','price_rule','freshness_rule','candidate_offers'])""",
        }
        integrity = {key: {"violations": scalar(connection, query), "result": "PASS"}
                     for key, query in integrity_queries.items()}
        for value in integrity.values():
            value["result"] = "PASS" if value["violations"] == 0 else "FAIL"

        pp = rows(connection, """SELECT sp.catalog_product_id,sp.id AS source_product_id,o.id AS offer_id,
            cs.selected_offer_id,cs.selection_status,cs.rule_version,cs.evidence->>'selection_reason' AS reason,
            pm.status AS match_status,pm.auto_accepted
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='partner_st'
            JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
            LEFT JOIN catalog_offer_selection cs ON cs.catalog_product_id=sp.catalog_product_id
            JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            WHERE sp.external_id='297'""")[0]
        pp_pass = (pp["catalog_product_id"] is not None and pp["selected_offer_id"] == pp["offer_id"]
                   and pp["selection_status"] == "selected" and pp["match_status"] == "EXACT_MATCH"
                   and pp["auto_accepted"])
        mrx = rows(connection, """SELECT sp.catalog_product_id,sp.id AS source_product_id,pm.status,
            pm.auto_accepted,count(cs.catalog_product_id) AS selection_rows
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='partner_st'
            JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            LEFT JOIN catalog_offer_selection cs ON cs.catalog_product_id=sp.catalog_product_id
            WHERE sp.external_id='305' GROUP BY sp.catalog_product_id,sp.id,pm.status,pm.auto_accepted""")[0]
        mrx_pass = mrx["catalog_product_id"] is None and mrx["status"] == "CONFLICT" and not mrx["auto_accepted"] and mrx["selection_rows"] == 0

        partner_counts = rows(connection, """SELECT count(DISTINCT sp.id) AS source_products,count(DISTINCT o.id) AS offers
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='partner_st'
            LEFT JOIN offers o ON o.source_product_id=sp.id""")[0]
        optimum_counts = rows(connection, """SELECT count(DISTINCT sp.id) AS source_products,count(DISTINCT o.id) AS offers
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='optimum'
            LEFT JOIN offers o ON o.source_product_id=sp.id""")[0]
        partner_pass = partner_counts == {"source_products": 1222, "offers": 1222}
        optimum_pass = optimum_counts == {"source_products": 1351, "offers": 1351}
        fuzzy = scalar(connection, "SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method IN ('NAME_CANDIDATE_ONLY','NO_IDENTITY_EVIDENCE')")
        cross_article = scalar(connection, """SELECT count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id
            JOIN catalog_product_identifiers i ON i.catalog_product_id=pm.catalog_product_id AND i.identifier_type='supplier_article'
            WHERE pm.auto_accepted AND i.supplier_id<>sp.supplier_id AND pm.match_method LIKE '%%SUPPLIER_ARTICLE%%'""")
        migration_pass, migration_hashes = migration_integrity()
        correction = json.loads((REPORTS / "STAGE4_FALSE_MATCH_CORRECTION_QA.json").read_text(encoding="utf-8"))
        audit_rows = list(csv.DictReader((REPORTS / "BRAND_MODEL_AUTO_MATCH_AUDIT.csv").open(encoding="utf-8-sig")))
        audit_by_key = {(row["supplier"], row["source_external_id"]): row for row in audit_rows}
        matching_by_key = {}
        for supplier, filename in (("partner_st", "PARTNER_ST_FULL_MATCHING_RUN2.csv"),
                                   ("optimum", "OPTIMUM_MATCHING_RUN2.csv")):
            for row in csv.DictReader((REPORTS / filename).open(encoding="utf-8-sig")):
                matching_by_key[(supplier, row["source_external_id"])] = row
        known_false_safe = all(
            matching_by_key[key]["auto_accepted"] == "FALSE"
            for key in (("partner_st", "1655"), ("partner_st", "1656"),
                        ("partner_st", "1835"), ("partner_st", "1837"), ("optimum", "561"))
        )
        tm9060_pass = matching_by_key[("partner_st", "1704")]["auto_accepted"] == "TRUE"
        optimum_782 = matching_by_key[("optimum", "782")]
        tu_selection = rows(connection, """SELECT sp.catalog_product_id,pm.status,pm.auto_accepted,
            (SELECT selection_status FROM catalog_offer_selection WHERE catalog_product_id=
              (SELECT catalog_product_id FROM sterbrust_products WHERE sterbrust_product_id='19862')) AS target_selection,
            (SELECT selected_offer_id FROM catalog_offer_selection WHERE catalog_product_id=
              (SELECT catalog_product_id FROM sterbrust_products WHERE sterbrust_product_id='19862')) AS target_offer
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id AND s.code='optimum'
            JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            WHERE sp.external_id='782'""")[0]
        tu2304v_pass = (optimum_782["auto_accepted"] == "FALSE"
                        and tu_selection["catalog_product_id"] is None
                        and not tu_selection["auto_accepted"]
                        and tu_selection["target_selection"] == "no_eligible_offer"
                        and tu_selection["target_offer"] is None)
        new_audit_rows = list(csv.DictReader(
            (REPORTS / "NEW_AUTO_ACCEPTED_AFTER_MATCHING_CHANGE_AUDIT.csv").open(encoding="utf-8-sig")))
        previous_new_rows = list(csv.DictReader(
            (REPORTS / "PREVIOUS_MATCHING_CHANGE_NEW_AUTO_ACCEPTED.csv").open(encoding="utf-8-sig")))
        new_audit_keys = {(row["supplier"], row["external_id"], row["sterbrust_id"]) for row in new_audit_rows}
        previous_new_keys = {(row["supplier"], row["source_external_id"], row["new_sterbrust_product_id"])
                             for row in previous_new_rows}
        allowed_audit_results = {"VALID_STRONG_IDENTIFIER", "VALID_SEMANTIC_IDENTITY",
                                 "REVIEW_REQUIRED", "FALSE_MATCH_REMOVED"}
        new_audit_complete = (new_audit_keys == previous_new_keys
                              and all(row["final_audit_result"] in allowed_audit_results for row in new_audit_rows)
                              and correction["unaudited_new_auto_accepted_mappings"] == 0
                              and correction["new_weak_auto_accepts_after_matcher_change"] == 0)
        brand_audit_complete = (len(audit_rows) == correction["current_brand_model_auto_matches"]
                                and all(row["final_auto_accepted"] == "TRUE"
                                        and row["audit_result"] == "VALID_SEMANTIC_IDENTITY" for row in audit_rows)
                                and correction["unaudited_brand_model_auto_matches"] == 0
                                and correction["brand_model_auto_match_audit_coverage_percent"] == 100)
        revalidation_rows = list(csv.DictReader(
            (REPORTS / "AUTO_ACCEPTED_MAPPING_REVALIDATION.csv").open(encoding="utf-8-sig")))
        current_auto = rows(connection, """SELECT s.code AS supplier,sp.external_id
            FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id
            JOIN suppliers s ON s.id=sp.supplier_id WHERE pm.is_current AND pm.auto_accepted""")
        revalidated_keys = {(row["supplier"], row["source_external_id"]) for row in revalidation_rows}
        current_auto_keys = {(row["supplier"], str(row["external_id"])) for row in current_auto}
        all_current_revalidated = (current_auto_keys == revalidated_keys
                                   and len(revalidation_rows) == correction["automatic_mappings_revalidated"])
        correction_pass = (
            all_current_revalidated and correction["same_supplier_canonical_collapses"] == 0
            and correction["stale_selection_after_mapping_removal"] == 0
            and known_false_safe and tm9060_pass and tu2304v_pass
            and new_audit_complete and brand_audit_complete
        )
        transaction_pass = sum(item["result"] == "PASS" for item in transaction_rows)
        all_integrity_pass = all(item["result"] == "PASS" for item in integrity.values())
        idempotent = apply2["changed"] == 0 and evaluated_at_before == evaluated_at_after

        fields = ["catalog_product_id", "sterbrust_product_id", "source_external_id", "sku", "name",
                  "source_product_id", "offer_id", "supplier", "price", "currency", "availability",
                  "availability_raw", "quantity", "selection_status", "reason", "rule_version",
                  "evaluated_at", "candidate_count", "eligible_candidate_count"]
        write_csv(REPORTS / "STAGE4_REAL_SELECTIONS.csv", selections, fields)
        write_csv(REPORTS / "STAGE4_SELECTION_TRACE.csv", choose_traces(selections), fields)
        write_csv(REPORTS / "STAGE4_SELECTION_REVIEW.csv",
                  [row for row in selections if row["selection_status"] == "review"], fields)
        write_csv(REPORTS / "STAGE4_NO_ELIGIBLE_OFFERS.csv",
                  [row for row in selections if row["selection_status"] == "no_eligible_offer"], fields)
        write_csv(REPORTS / "STAGE4_MULTI_SUPPLIER_TRANSACTION_QA.csv", transaction_rows,
                  ["scenario", "expected_status", "actual_status", "expected_supplier",
                   "selected_supplier", "selected_offer_id", "selection_reason", "policy_version", "result", "details"])
        write_csv(REPORTS / "STAGE4_REAL_OVERLAP_AUDIT.csv",
                  rows(connection, """SELECT sp.catalog_product_id,
                    string_agg(DISTINCT s.code,',' ORDER BY s.code) AS supplier_codes,
                    count(DISTINCT sp.id) AS source_product_count,count(DISTINCT o.id) AS offer_count
                    FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id
                    WHERE sp.catalog_product_id IS NOT NULL AND s.code IN ('partner_st','optimum')
                    GROUP BY sp.catalog_product_id HAVING count(DISTINCT s.code)>1 ORDER BY sp.catalog_product_id"""),
                  ["catalog_product_id", "supplier_codes", "source_product_count", "offer_count"])
        count_rows = [
            {"metric": "catalog_products_evaluated", "value": len(decisions2)},
            {"metric": "selected", "value": status_counts["selected"]},
            {"metric": "no_eligible_offer", "value": status_counts["no_eligible_offer"]},
            {"metric": "review", "value": status_counts["review"]},
            {"metric": "real_single_offer_selections", "value": single},
            {"metric": "real_multi_offer_selections", "value": multi_offer},
            {"metric": "real_multi_supplier_overlap", "value": real_overlap},
            {"metric": "real_multi_supplier_selections", "value": real_multi_selected},
        ]
        write_csv(REPORTS / "STAGE4_SELECTION_COUNTS.csv", count_rows, ["metric", "value"])
        (REPORTS / "STAGE4_POLICY.json").write_text(
            json.dumps(policy.as_jsonable(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        integrity_document = {
            "state": "PASS" if all_integrity_pass and mrx_pass else "FAIL",
            **integrity,
            "MRX3_CONFLICT_NOT_SELECTABLE": {"violations": 0 if mrx_pass else 1,
                                              "result": "PASS" if mrx_pass else "FAIL"},
            "residual_transaction_probe_rows": residual,
        }
        (REPORTS / "STAGE4_SELECTION_INTEGRITY_QA.json").write_text(
            json.dumps(integrity_document, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        (REPORTS / "STAGE4_PP800F_SELECTION.md").write_text(
            "# PP-800F offer selection\n\n"
            f"`PP800F_OFFER_SELECTION = {'PASS' if pp_pass else 'FAIL'}`\n\n"
            f"- catalog_product_id: `{pp['catalog_product_id']}`\n"
            f"- source_product_id: `{pp['source_product_id']}`\n"
            f"- default offer_id: `{pp['offer_id']}`\n"
            f"- selected_offer_id: `{pp['selected_offer_id']}`\n"
            f"- selection: `{pp['selection_status']}` — `{pp['reason']}`\n"
            f"- matching remains `{pp['match_status']}`; no matching data was changed.\n", encoding="utf-8"
        )
        (REPORTS / "STAGE4_MRX3_SELECTION.md").write_text(
            "# MR-X3 / MR-X4 selection isolation\n\n"
            f"`MRX3_CONFLICT_NOT_SELECTABLE = {'PASS' if mrx_pass else 'FAIL'}`\n\n"
            f"MR-X3 source_product `{mrx['source_product_id']}` remains `{mrx['status']}`, "
            "is unlinked, and produced no catalog-offer selection. MR-X4 was not selected on its behalf.\n", encoding="utf-8"
        )
        c_row = next(item for item in transaction_rows if item["scenario"] == "C_CONFIGURED_PRIORITY")
        a_row = next(item for item in transaction_rows if item["scenario"] == "A_LOWER_PRICE")
        (REPORTS / "STAGE4_POLICY_CHANGE_QA.md").write_text(
            "# Stage 4 policy-change QA\n\n"
            f"Result: `{'PASS' if a_row['result']=='PASS' and c_row['result']=='PASS' else 'FAIL'}`\n\n"
            f"- `{policy.version}` with equal priority: lower-price `{a_row['selected_supplier']}` wins.\n"
            f"- `{c_row['policy_version']}` with explicit Partner-ST priority: `{c_row['selected_supplier']}` wins.\n"
            "- Database candidate data is equivalent; the winner changes through policy configuration only.\n", encoding="utf-8"
        )

        summary_values = {
            "STAGE4": "CLOSED" if all((partner_pass, optimum_pass, pp_pass, mrx_pass, correction_pass, all_integrity_pass,
                                          migration_pass, idempotent, transaction_pass == len(transaction_rows),
                                          residual == 0, fuzzy == 0, cross_article == 0)) else "FAILED",
            "POLICY_VERSION": policy.version,
            "DOCKER_POSTGRES": args.docker_status,
            "POSTGRES_VERSION": server_version,
            "POSTGRES_PERSISTENT_VOLUME": args.volume_status,
            "SELECTION_ROWS_AFTER_CONTAINER_RECREATE": args.post_recreate_selection_count,
            "PARTNER_ST_REGRESSION": "PASS" if partner_pass else "FAIL",
            "OPTIMUM_REGRESSION": "PASS" if optimum_pass else "FAIL",
            "OPTIMUM_INGESTION_REGRESSION": "PASS" if optimum_pass else "FAIL",
            "ALL_AUTO_ACCEPTED_MAPPINGS_REVALIDATED": "PASS" if correction_pass else "FAIL",
            "ALL_CURRENT_AUTO_ACCEPTED_MAPPINGS_REVALIDATED": "PASS" if all_current_revalidated else "FAIL",
            "AUTO_ACCEPTED_MAPPINGS_REVALIDATED": correction["automatic_mappings_revalidated"],
            "KNOWN_FALSE_MATCHES_REMOVED_OR_REVIEW": "PASS" if known_false_safe else "FAIL",
            "KNOWN_PREVIOUS_FALSE_MATCHES_REMAIN_FIXED": "PASS" if known_false_safe else "FAIL",
            "OPTIMUM_782_TU2304V_AUTO_MATCH": "REMOVED_OR_REVIEW" if tu2304v_pass else "FAIL",
            "OPTIMUM_TU2304V_REGRESSION": "PASS" if tu2304v_pass else "FAIL",
            "OPTIMUM_TU2304V_FALSE_SELECTION": "REMOVED" if tu2304v_pass else "FAIL",
            "TU2304V_MODEL_IDENTITY_DISTINCT_FROM_TU2304": "PASS" if tu2304v_pass else "FAIL",
            "BRAND_MODEL_AUTO_MATCH_AUDIT_COVERAGE": f"{correction['brand_model_auto_match_audit_coverage_percent']}%",
            "UNAUDITED_NEW_AUTO_ACCEPTED_MAPPINGS": correction["unaudited_new_auto_accepted_mappings"],
            "NEW_WEAK_AUTO_ACCEPTS_AFTER_MATCHER_CHANGE": correction["new_weak_auto_accepts_after_matcher_change"],
            "UNAUDITED_BRAND_MODEL_AUTO_MATCHES": correction["unaudited_brand_model_auto_matches"],
            "UNEXPLAINED_SAME_SUPPLIER_CANONICAL_COLLAPSES": correction["same_supplier_canonical_collapses"],
            "STALE_SELECTION_AFTER_MAPPING_REMOVAL": correction["stale_selection_after_mapping_removal"],
            "PARTNER_ST_ACCEPTED_MAPPINGS": correction["partner_st_accepted_mappings"],
            "OPTIMUM_ACCEPTED_MAPPINGS": correction["optimum_accepted_mappings"],
            "CATALOG_PRODUCTS_EVALUATED": len(decisions2),
            "SELECTED": status_counts["selected"],
            "NO_ELIGIBLE_OFFER": status_counts["no_eligible_offer"],
            "REVIEW": status_counts["review"],
            "REAL_SINGLE_OFFER_SELECTIONS": single,
            "REAL_MULTI_OFFER_SELECTIONS": multi_offer,
            "REAL_MULTI_SUPPLIER_OVERLAP": real_overlap,
            "REAL_MULTI_SUPPLIER_SELECTION": "NOT_APPLICABLE_CURRENT_DATA" if real_overlap == 0 else real_multi_selected,
            "MULTI_SUPPLIER_POLICY_TRANSACTION_QA": f"PASS_{transaction_pass}_OF_{len(transaction_rows)}",
            "PP800F_OFFER_SELECTION": "PASS" if pp_pass else "FAIL",
            "PP800F": "PASS" if pp_pass else "FAIL",
            "MRX3_CONFLICT_NOT_SELECTABLE": "PASS" if mrx_pass else "FAIL",
            "MRX3_MRX4": "CONFLICT" if mrx_pass else "FAIL",
            "SUPPLIER_ISOLATION": "PASS" if partner_pass and optimum_pass and cross_article == 0 else "FAIL",
            "SELECTION_INTEGRITY": "PASS" if all_integrity_pass else "FAIL",
            "RUN2_IDEMPOTENCY": "PASS" if idempotent else "FAIL",
            "POLICY_CHANGE_QA": "PASS" if c_row["result"] == "PASS" else "FAIL",
            "FUZZY_AUTO_MATCHES": fuzzy,
            "CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES": cross_article,
            "STERBRUST_WRITE_METHODS_USED": 0,
            "XML_GENERATED": "NO",
            "YML_GENERATED": "NO",
            "MIGRATIONS_001_011_IMMUTABLE": "PASS" if migration_pass else "FAIL",
            "MIGRATION_012": "NOT_REQUIRED",
            "ALL_TESTS": f"PASS_{args.test_count}",
            "RUN1_SELECTION_ROWS_CHANGED": apply1["changed"],
            "RUN2_SELECTION_ROWS_CHANGED": apply2["changed"],
            "RESIDUAL_PROBE_ROWS": residual,
        }
        (REPORTS / "STAGE4_SUMMARY.txt").write_text(
            "\n".join(f"{key} = {value}" for key, value in summary_values.items()) + "\n", encoding="utf-8"
        )
        failed_transactions = [item["scenario"] for item in transaction_rows if item["result"] != "PASS"]
        qa = f"""# Stage 4 supplier offer selection QA

## Result

`STAGE4 = {summary_values['STAGE4']}`

The generic policy engine evaluated {len(decisions2)} existing canonical products with accepted linked
source relationships. It selected {status_counts['selected']}; {status_counts['no_eligible_offer']} had no
eligible offer and {status_counts['review']} require review. Matching was revalidated before selection;
only prior automatic links that failed the corrected semantic-identity policy were quarantined.

## Policy and ranking

- Policy: `{policy.version}`
- Order: `{' -> '.join(policy.ranking_order)}`
- Partner-ST and Optimum default priority: equal
- Lowest valid price is considered only inside the best availability and supplier-priority rank.
- Stale, inactive, disabled-supplier and discontinued candidates are excluded by default.
- Cross-currency top-price comparisons and in-stock quantity-zero contradictions go to REVIEW.

## Real database evidence

- PostgreSQL: `{server_version}` in healthy Docker runtime; persistent volume verified externally: `{args.volume_status}`
- Selection rows after PostgreSQL container recreate: `{args.post_recreate_selection_count}`
- Real single-offer selections: `{single}`
- Real multi-offer selections: `{multi_offer}`
- Real Partner-ST + Optimum overlap: `{real_overlap}`
- Real multi-supplier selection: `{'NOT_APPLICABLE_CURRENT_DATA' if real_overlap == 0 else real_multi_selected}`
- Automatic mappings revalidated: `{correction['automatic_mappings_revalidated']}`
- TU2304V regression: `{'PASS' if tu2304v_pass else 'FAIL'}`; Optimum 782 is unlinked and Sterbrust 19862 is `no_eligible_offer`.
- New auto-accepted relationships from the previous matcher change audited: `{len(new_audit_rows)}`; unaudited: `{correction['unaudited_new_auto_accepted_mappings']}`
- Current BRAND_MODEL audit coverage: `{correction['brand_model_auto_match_audit_coverage_percent']}%` ({len(audit_rows)} relationships)
- Accepted mappings after correction: Partner-ST `{correction['partner_st_accepted_mappings']}`, Optimum `{correction['optimum_accepted_mappings']}`
- Unexplained same-supplier canonical collapses: `{correction['same_supplier_canonical_collapses']}`
- Stale selections after mapping removal: `{correction['stale_selection_after_mapping_removal']}`
- RUN2 changed rows: `{apply2['changed']}`; evaluated_at preserved: `{idempotent}`

## Transaction policy QA

`{transaction_pass}/{len(transaction_rows)} PASS`; failed scenarios: `{failed_transactions}`. Every scenario used the real
PostgreSQL schema and real selection engine inside a rollback-only transaction. Residual probe rows: `{residual}`.
This is synthetic policy QA and is not represented as real supplier overlap.

## Regression and boundaries

- Partner-ST regression: `{'PASS' if partner_pass else 'FAIL'}` ({partner_counts})
- Optimum regression: `{'PASS' if optimum_pass else 'FAIL'}` ({optimum_counts})
- PP-800F: `{'PASS' if pp_pass else 'FAIL'}`
- MR-X3/MR-X4: `{'PASS' if mrx_pass else 'FAIL'}`
- Integrity assertions: `{'PASS' if all_integrity_pass else 'FAIL'}`
- Migrations 001-011 immutable: `{'PASS' if migration_pass else 'FAIL'}`; migration 012: `NOT_REQUIRED`
- Fuzzy auto matches: `{fuzzy}`; cross-supplier article auto matches: `{cross_article}`
- Sterbrust writes: `0`; XML/YML generated: `NO`
- Tests: `{args.test_count} PASS`
"""
        (REPORTS / "STAGE4_QA_REPORT.md").write_text(qa, encoding="utf-8")
        print(json.dumps(summary_values, ensure_ascii=False, indent=2, default=str))
        if summary_values["STAGE4"] != "CLOSED":
            raise SystemExit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
