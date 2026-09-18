#!/usr/bin/env python3
"""Revalidate automatic mappings, rerun matching, and rebuild Stage 4 selection."""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))

from sterbrust_matching.normalization import model_identity_evidence, model_tokens, normalize_model, normalized_name
from universal_supplier.matching_service import load_registry_from_postgres, match_cards
from universal_supplier.models import ProductCard
from universal_supplier.offer_selection import OfferSelectionPolicy, PostgresOfferSelectionStore, evaluate_offers
from universal_supplier.postgres import PostgresConfig, PostgresRepository

MATCH_FIELDS = [
    "source_external_id", "SKU", "name", "brand", "model", "status",
    "match_method", "confidence_score", "sterbrust_product_id", "conflict_class",
    "warnings", "data_quality_flags", "conflicts", "evidence", "auto_accepted",
]
PREVIOUS_CHANGE_BASELINE = REPORTS / "PREVIOUS_MATCHING_CHANGE_NEW_AUTO_ACCEPTED.csv"


def write_csv(path: Path, records: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def load_cards(supplier: str) -> list[ProductCard]:
    filename = "PARTNER_ST_PARSED_RUN2.jsonl" if supplier == "partner_st" else "OPTIMUM_PARSED_RUN2.jsonl"
    with (REPORTS / filename).open(encoding="utf-8") as handle:
        return [ProductCard.from_jsonable(json.loads(line)) for line in handle if line.strip()]


def scalar(connection, query: str, params=()):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def mapping_state(connection, supplier: str) -> dict[str, int | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT sp.external_id,sp.catalog_product_id FROM source_products sp
               JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s""", (supplier,)
        )
        return {str(external_id): catalog_id for external_id, catalog_id in cursor.fetchall()}


def current_accepted_count(connection, supplier: str) -> int:
    return scalar(connection, """SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        WHERE s.code=%s AND sp.catalog_product_id IS NOT NULL""", (supplier,))


def current_decisions(connection, supplier: str) -> dict[str, dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT sp.external_id,pm.status,pm.match_method,pm.auto_accepted,
                      coalesce(sb.sterbrust_product_id,'')
               FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
               LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
               LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=pm.catalog_product_id
               WHERE s.code=%s AND sp.external_id_is_stable""", (supplier,),
        )
        return {str(row[0]): {"status": row[1] or "", "method": row[2] or "",
                              "auto_accepted": bool(row[3]), "sterbrust_product_id": str(row[4] or "")}
                for row in cursor.fetchall()}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def previous_change_baseline(decisions_before: dict[str, dict[str, dict]]) -> list[dict]:
    """Freeze the generic before/after set difference supplied by correction #1.

    The authoritative checkpoint's RUN2 files are its AFTER snapshot, while the
    current persisted decisions are the BEFORE snapshot.  Once derived, keep the
    compact difference so reruns never depend on already-overwritten reports.
    """
    if PREVIOUS_CHANGE_BASELINE.exists():
        return read_csv(PREVIOUS_CHANGE_BASELINE)
    rows = []
    for supplier, filename in (("partner_st", "PARTNER_ST_FULL_MATCHING_RUN2.csv"),
                               ("optimum", "OPTIMUM_MATCHING_RUN2.csv")):
        for after in read_csv(REPORTS / filename):
            if after["auto_accepted"] != "TRUE":
                continue
            before = decisions_before[supplier].get(after["source_external_id"], {})
            same_relationship = (before.get("auto_accepted")
                                 and before.get("sterbrust_product_id") == after["sterbrust_product_id"])
            if same_relationship:
                continue
            rows.append({
                "supplier": supplier, "source_external_id": after["source_external_id"],
                "SKU": after["SKU"], "source_title": after["name"],
                "old_status": before.get("status", ""), "old_method": before.get("method", ""),
                "old_auto_accepted": str(bool(before.get("auto_accepted"))).upper(),
                "old_sterbrust_product_id": before.get("sterbrust_product_id", ""),
                "new_status": after["status"], "new_method": after["match_method"],
                "new_sterbrust_product_id": after["sterbrust_product_id"],
            })
    fields = ["supplier", "source_external_id", "SKU", "source_title", "old_status",
              "old_method", "old_auto_accepted", "old_sterbrust_product_id", "new_status",
              "new_method", "new_sterbrust_product_id"]
    write_csv(PREVIOUS_CHANGE_BASELINE, rows, fields)
    return rows


def main() -> None:
    if os.environ.get("STAGE4_DB_CONFIRM") != "YES":
        raise SystemExit("Set STAGE4_DB_CONFIRM=YES for the selected Stage 4 Docker database")
    repo = PostgresRepository(PostgresConfig.from_env())
    validated_at = datetime.now(timezone.utc).isoformat()
    try:
        registry = load_registry_from_postgres(repo.connection)
        cards = {supplier: load_cards(supplier) for supplier in ("partner_st", "optimum")}
        decisions_before = {supplier: current_decisions(repo.connection, supplier) for supplier in cards}
        previous_new_accepts = previous_change_baseline(decisions_before)
        contexts_before = {supplier: repo.load_existing_link_contexts(supplier)
                           for supplier in cards}
        revalidation: dict[str, list[dict]] = {}

        # Pass 1 revalidates every accepted mapping using its database lineage.
        for supplier, supplier_cards in cards.items():
            rows = match_cards(supplier_cards, registry, contexts_before[supplier])
            revalidation[supplier] = rows
            for row in rows:
                if row["source_external_id"] in contexts_before[supplier]:
                    repo.persist_match(row, validated_at, supplier_code=supplier)

        state_after_revalidation = {supplier: mapping_state(repo.connection, supplier) for supplier in cards}

        # Fresh candidate pass is exported as corrected RUN1 evidence. It does
        # not bypass persistence provenance or mutate manual relationships.
        fresh = {supplier: match_cards(supplier_cards, registry, {})
                 for supplier, supplier_cards in cards.items()}

        # Pass 2 uses only links that survived revalidation. Invalid links are
        # therefore reported as REVIEW/CONFLICT/NEW_CANDIDATE, never EXISTING_LINK.
        final: dict[str, list[dict]] = {}
        for supplier, supplier_cards in cards.items():
            contexts = repo.load_existing_link_contexts(supplier)
            rows = match_cards(supplier_cards, registry, contexts)
            final[supplier] = rows
            for row in rows:
                repo.persist_match(row, validated_at, supplier_code=supplier)

        for supplier, prefix in (("partner_st", "PARTNER_ST_FULL_MATCHING"),
                                 ("optimum", "OPTIMUM_MATCHING")):
            write_csv(REPORTS / f"{prefix}_RUN1.csv", fresh[supplier], MATCH_FIELDS)
            write_csv(REPORTS / f"{prefix}_RUN2.csv", final[supplier], MATCH_FIELDS)

        revalidation_rows = []
        for supplier in cards:
            by_id = {row["source_external_id"]: row for row in revalidation[supplier]}
            for external_id, context in sorted(contexts_before[supplier].items(), key=lambda item: int(item[0])):
                if not context["current_auto_accepted"]:
                    continue
                row = by_id[external_id]
                linked_after = state_after_revalidation[supplier][external_id] is not None
                if row["auto_accepted"] == "TRUE":
                    outcome = "STILL_VALID"
                elif row["status"] == "REVIEW":
                    outcome = "DOWNGRADED_TO_REVIEW"
                elif row["status"] == "CONFLICT":
                    outcome = "CONFLICT"
                else:
                    outcome = "UNLINKED"
                revalidation_rows.append({
                    "supplier": supplier, "source_external_id": external_id,
                    "SKU": row["SKU"], "source_name": row["name"],
                    "previous_sterbrust_product_id": context["sterbrust_product_id"],
                    "origin_match_method": context["origin_match_method"],
                    "revalidation_status": row["status"], "revalidation_method": row["match_method"],
                    "outcome": outcome, "linked_after_revalidation": str(linked_after).upper(),
                    "evidence": row["evidence"], "conflicts": row["conflicts"],
                })
        revalidation_fields = [
            "supplier", "source_external_id", "SKU", "source_name",
            "previous_sterbrust_product_id", "origin_match_method", "revalidation_status",
            "revalidation_method", "outcome", "linked_after_revalidation", "evidence", "conflicts",
        ]
        write_csv(REPORTS / "AUTO_ACCEPTED_MAPPING_REVALIDATION.csv",
                  revalidation_rows, revalidation_fields)

        new_accept_audits = []
        for baseline in previous_new_accepts:
            supplier, external_id = baseline["supplier"], baseline["source_external_id"]
            card = next(card for card in cards[supplier] if card.external_id == external_id)
            corrected = next(row for row in final[supplier] if row["source_external_id"] == external_id)
            target = registry.products[baseline["new_sterbrust_product_id"]]
            source_identity = model_identity_evidence(card.name)
            candidate_identity = model_identity_evidence(target["name"])
            source_model = normalize_model(corrected["model"], card.brand)
            candidate_model = normalize_model(target["model"], target["brand"])
            supplier_values = tuple(target["supplier_identifiers"].get(supplier, ()))
            normalized_sku = normalize_model(card.sku)
            article_ids = registry.identifier_index.get((supplier, normalized_sku), [])
            supplier_article_evidence = {
                "source_sku": card.sku, "candidate_values": supplier_values,
                "normalized_sku": normalized_sku, "unique_candidate_count": len(article_ids),
                "candidate_is_unique_exact": article_ids == [baseline["new_sterbrust_product_id"]],
            }
            similarity = round(SequenceMatcher(None, normalized_name(card.name),
                                               normalized_name(target["name"])).ratio(), 4)
            if corrected["auto_accepted"] != "TRUE":
                audit_result = "FALSE_MATCH_REMOVED"
                reason = "Significant model execution differs; the previous automatic relationship is quarantined."
            elif (baseline["new_method"] == "SUPPLIER_ARTICLE_EXACT"
                  and supplier_article_evidence["candidate_is_unique_exact"]):
                audit_result = "VALID_STRONG_IDENTIFIER"
                reason = "Unique exact supplier-namespaced article; descriptive differences remain visible as data-quality evidence."
            elif (baseline["new_method"] == "BRAND_MODEL"
                  and source_model == candidate_model and candidate_model in model_tokens(target["name"])
                  and source_identity["model_ownership"] == candidate_identity["model_ownership"] == "OWN_PRODUCT"):
                audit_result = "VALID_SEMANTIC_IDENTITY"
                reason = "Brand, full execution-aware model identity, and own-product title semantics agree without contradiction."
            else:
                audit_result = "REVIEW_REQUIRED"
                reason = "Automatic acceptance lacks approved strong-identifier or full semantic-identity evidence."
            new_accept_audits.append({
                "supplier": supplier, "external_id": external_id, "SKU": card.sku,
                "source_title": card.name, "sterbrust_id": baseline["new_sterbrust_product_id"],
                "sterbrust_title": target["name"],
                "old_decision": f"{baseline['old_status']} / {baseline['old_method']} / AUTO={baseline['old_auto_accepted']}",
                "new_decision": f"{baseline['new_status']} / {baseline['new_method']} / AUTO=TRUE",
                "match_method": baseline["new_method"],
                "supplier_article_evidence": json.dumps(supplier_article_evidence, ensure_ascii=False, sort_keys=True),
                "source_model_identity": json.dumps({**source_identity, "matching_model": source_model}, ensure_ascii=False, sort_keys=True),
                "candidate_model_identity": json.dumps({**candidate_identity, "declared_model": target["model"], "matching_model": candidate_model}, ensure_ascii=False, sort_keys=True),
                "name_similarity": similarity, "reason": reason, "final_audit_result": audit_result,
                "corrected2_status": corrected["status"], "corrected2_method": corrected["match_method"],
                "corrected2_auto_accepted": corrected["auto_accepted"],
                "corrected2_sterbrust_id": corrected["sterbrust_product_id"],
            })
        new_audit_fields = [
            "supplier", "external_id", "SKU", "source_title", "sterbrust_id", "sterbrust_title",
            "old_decision", "new_decision", "match_method", "supplier_article_evidence",
            "source_model_identity", "candidate_model_identity", "name_similarity", "reason",
            "final_audit_result", "corrected2_status", "corrected2_method",
            "corrected2_auto_accepted", "corrected2_sterbrust_id",
        ]
        write_csv(REPORTS / "NEW_AUTO_ACCEPTED_AFTER_MATCHING_CHANGE_AUDIT.csv",
                  new_accept_audits, new_audit_fields)

        # Audit the full current accepted BRAND_MODEL set.  Existing links use
        # their preserved origin; mappings created in this run expose BRAND_MODEL
        # directly in the final decision.  No fixed count or fixed ID list is used.
        brand_keys = set()
        for supplier in cards:
            for row in final[supplier]:
                if row["auto_accepted"] != "TRUE":
                    continue
                origin = contexts_before[supplier].get(row["source_external_id"], {}).get("origin_match_method", "")
                if row["match_method"] == "BRAND_MODEL" or origin == "BRAND_MODEL":
                    brand_keys.add((supplier, row["source_external_id"]))
        for baseline in previous_new_accepts:
            if baseline["new_method"] != "BRAND_MODEL":
                continue
            row = next(item for item in final[baseline["supplier"]]
                       if item["source_external_id"] == baseline["source_external_id"])
            if row["auto_accepted"] == "TRUE":
                brand_keys.add((baseline["supplier"], baseline["source_external_id"]))
        brand_audits = []
        for supplier, external_id in sorted(brand_keys):
            card = next(card for card in cards[supplier] if card.external_id == external_id)
            corrected = next(row for row in final[supplier] if row["source_external_id"] == external_id)
            target = registry.products[corrected["sterbrust_product_id"]]
            source_identity, target_identity = model_identity_evidence(card.name), model_identity_evidence(target["name"])
            source_model = normalize_model(corrected["model"], card.brand)
            target_model = normalize_model(target["model"], target["brand"])
            semantic_pass = (source_model == target_model and target_model in model_tokens(target["name"])
                             and source_identity["model_ownership"] == target_identity["model_ownership"] == "OWN_PRODUCT")
            brand_audits.append({
                "supplier": supplier, "source_external_id": external_id, "SKU": card.sku,
                "source_name": card.name,
                "source_model_identity": json.dumps({**source_identity, "matching_model": source_model}, ensure_ascii=False, sort_keys=True),
                "sterbrust_product_id": corrected["sterbrust_product_id"], "sterbrust_name": target["name"],
                "sterbrust_article": target["article"], "sterbrust_model_raw": target["model"],
                "sterbrust_model_identity": json.dumps({**target_identity, "declared_model": target["model"], "matching_model": target_model}, ensure_ascii=False, sort_keys=True),
                "name_similarity": round(SequenceMatcher(None, normalized_name(card.name), normalized_name(target["name"])).ratio(), 4),
                "final_status": corrected["status"], "final_method": corrected["match_method"],
                "final_auto_accepted": corrected["auto_accepted"],
                "audit_result": "VALID_SEMANTIC_IDENTITY" if semantic_pass else "REVIEW_REQUIRED",
                "audit_reason": "Full execution-aware model identity and own-product semantics agree." if semantic_pass else "Semantic identity evidence is incomplete.",
            })
        brand_fields = ["supplier", "source_external_id", "SKU", "source_name", "source_model_identity",
                        "sterbrust_product_id", "sterbrust_name", "sterbrust_article", "sterbrust_model_raw",
                        "sterbrust_model_identity", "name_similarity", "final_status", "final_method",
                        "final_auto_accepted", "audit_result", "audit_reason"]
        write_csv(REPORTS / "BRAND_MODEL_AUTO_MATCH_AUDIT.csv", brand_audits, brand_fields)

        policy = OfferSelectionPolicy.load(ROOT / "config" / "offer_selection.json")
        store = PostgresOfferSelectionStore(repo.connection)
        decisions = store.evaluate_all(policy)
        evaluated_ids = {decision.catalog_product_id for decision in decisions}
        # A relationship introduced by correction #1 may exist in its persisted
        # report snapshot even when the local reusable volume was restored to the
        # preceding baseline.  Explicitly carry every now-removed relationship's
        # canonical product into evaluation so it deterministically transitions
        # to no_eligible_offer instead of retaining or omitting a stale winner.
        for audit in new_accept_audits:
            if audit["final_audit_result"] != "FALSE_MATCH_REMOVED":
                continue
            catalog_id = scalar(repo.connection,
                                "SELECT catalog_product_id FROM sterbrust_products WHERE sterbrust_product_id=%s",
                                (audit["sterbrust_id"],))
            if catalog_id not in evaluated_ids:
                decisions.append(evaluate_offers(catalog_id, [], policy))
                evaluated_ids.add(catalog_id)
        apply_result = store.apply(decisions)

        with repo.connection.cursor() as cursor:
            cursor.execute("""SELECT sp.catalog_product_id,coalesce(sb.sterbrust_product_id,''),s.code,
                string_agg(sp.external_id, '|' ORDER BY sp.external_id::bigint),
                string_agg(coalesce(sp.sku,''), '|' ORDER BY sp.external_id::bigint),
                string_agg(sp.name, ' || ' ORDER BY sp.external_id::bigint),
                string_agg(o.id::text, '|' ORDER BY sp.external_id::bigint),
                string_agg(coalesce(pm.match_method,''), '|' ORDER BY sp.external_id::bigint),
                count(DISTINCT sp.id),coalesce(cs.selection_status,''),
                coalesce(cs.evidence->>'selection_reason','')
              FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
              JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default' AND o.active
              LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
              LEFT JOIN catalog_offer_selection cs ON cs.catalog_product_id=sp.catalog_product_id
              LEFT JOIN LATERAL (SELECT sterbrust_product_id FROM sterbrust_products x
                WHERE x.catalog_product_id=sp.catalog_product_id ORDER BY x.id LIMIT 1) sb ON true
              WHERE sp.catalog_product_id IS NOT NULL AND sp.active
              GROUP BY sp.catalog_product_id,sb.sterbrust_product_id,s.code,cs.selection_status,cs.evidence
              HAVING count(DISTINCT sp.id)>1 ORDER BY sp.catalog_product_id,s.code""")
            collapse_rows = [{
                "catalog_product_id": row[0], "sterbrust_product_id": row[1], "supplier": row[2],
                "source_external_ids": row[3], "SKUs": row[4], "source_names": row[5],
                "offer_ids": row[6], "matching_origins": row[7], "source_product_count": row[8],
                "selection_status": row[9], "selection_reason": row[10],
            } for row in cursor.fetchall()]
        collapse_fields = ["catalog_product_id", "sterbrust_product_id", "supplier", "source_external_ids",
                           "SKUs", "source_names", "offer_ids", "matching_origins",
                           "source_product_count", "selection_status", "selection_reason"]
        write_csv(REPORTS / "SAME_SUPPLIER_CANONICAL_COLLAPSE_AUDIT.csv", collapse_rows, collapse_fields)

        stale = scalar(repo.connection, """SELECT count(*) FROM catalog_offer_selection cs
            JOIN offers o ON o.id=cs.selected_offer_id JOIN source_products sp ON sp.id=o.source_product_id
            WHERE cs.selection_status='selected' AND
              (sp.catalog_product_id IS NULL OR sp.catalog_product_id<>cs.catalog_product_id)""")
        counts = Counter(decision.selection_status for decision in decisions)
        unaudited_new = sum(row["final_audit_result"] not in {
            "VALID_STRONG_IDENTIFIER", "VALID_SEMANTIC_IDENTITY", "REVIEW_REQUIRED", "FALSE_MATCH_REMOVED"
        } for row in new_accept_audits)
        weak_new_auto = sum(row["corrected2_auto_accepted"] == "TRUE" and row["final_audit_result"] not in {
            "VALID_STRONG_IDENTIFIER", "VALID_SEMANTIC_IDENTITY"
        } for row in new_accept_audits)
        unaudited_brand = sum(row["audit_result"] != "VALID_SEMANTIC_IDENTITY" for row in brand_audits)
        summary = {
            "automatic_mappings_revalidated": len(revalidation_rows),
            "revalidation_outcomes": dict(Counter(row["outcome"] for row in revalidation_rows)),
            "partner_st_accepted_mappings": current_accepted_count(repo.connection, "partner_st"),
            "optimum_accepted_mappings": current_accepted_count(repo.connection, "optimum"),
            "same_supplier_canonical_collapses": len(collapse_rows),
            "catalog_products_evaluated": len(decisions), "selection_status_counts": dict(counts),
            "selection_apply": apply_result, "stale_selection_after_mapping_removal": stale,
            "previous_matching_change_new_auto_accepted": len(previous_new_accepts),
            "new_auto_accepted_audit_rows": len(new_accept_audits),
            "unaudited_new_auto_accepted_mappings": unaudited_new,
            "new_weak_auto_accepts_after_matcher_change": weak_new_auto,
            "current_brand_model_auto_matches": len(brand_audits),
            "unaudited_brand_model_auto_matches": unaudited_brand,
            "brand_model_auto_match_audit_coverage_percent": 100 if len(brand_audits) == 0 or unaudited_brand == 0 else 0,
            "optimum_782_tu2304v_auto_match": "REMOVED_OR_REVIEW" if any(
                row["supplier"] == "optimum" and row["external_id"] == "782"
                and row["final_audit_result"] == "FALSE_MATCH_REMOVED" for row in new_accept_audits) else "FAIL",
            "optimum_tu2304v_false_selection": "REMOVED" if stale == 0 else "FAIL",
            "tu2304v_model_identity_distinct_from_tu2304": "PASS" if any(
                row["supplier"] == "optimum" and row["external_id"] == "782"
                and json.loads(row["source_model_identity"])["matching_model"] == "tu2304v"
                and json.loads(row["candidate_model_identity"])["matching_model"] == "tu2304"
                for row in new_accept_audits) else "FAIL",
        }
        (REPORTS / "STAGE4_FALSE_MATCH_CORRECTION_QA.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if stale or collapse_rows or unaudited_new or weak_new_auto or unaudited_brand:
            raise SystemExit("Identity correction QA failed")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
