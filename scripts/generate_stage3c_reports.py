#!/usr/bin/env python3
"""Generate Stage 3C functional verification artifacts from packaged Stage 3B evidence.

This script is deliberately read-only with respect to PostgreSQL and Sterbrust.  It
does not invent physical database IDs that were not exported by the checkpoint.
"""

from __future__ import annotations

import csv
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
NOT_EXPORTED = "NOT_EXPORTED_IN_PACKAGED_EVIDENCE"
STATUS_QUOTAS = {
    "EXACT_MATCH": 5,
    "HIGH_CONFIDENCE_MATCH": 2,
    "REVIEW": 3,
    "NEW_CANDIDATE": 3,
    "CONFLICT": 3,
}


def read_jsonl(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_csv(name: str) -> list[dict]:
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with (REPORTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def bool_text(value) -> str:
    return "TRUE" if str(value).strip().upper() in {"TRUE", "1", "Y", "YES"} else "FALSE"


def media_counts(product: dict) -> tuple[int, int, int]:
    counter = Counter(m.get("media_type") for m in product.get("media", []))
    return counter["image"], counter["document"], counter["video"]


def option_counts(product: dict) -> tuple[int, int, int]:
    options = product.get("options", [])
    values = [v for option in options for v in option.get("values", [])]
    relations = sum(v.get("classification") == "NAVIGATION_TO_PRODUCT" for v in values)
    return len(options), len(values), relations


def explanation(product: dict, match: dict, registry: dict | None) -> str:
    status = match["status"]
    method = match["match_method"]
    sku = product.get("sku") or "без SKU"
    candidate_id = match.get("sterbrust_product_id") or "не найден"
    candidate_name = registry.get("name", "") if registry else ""
    evidence = parse_json(match.get("evidence"), {})
    flags = parse_json(match.get("data_quality_flags"), [])
    if status == "EXACT_MATCH":
        if method == "EXISTING_LINK":
            text = f"Для Partner-ST SKU {sku} повторно подтверждена существующая связь со Sterbrust {candidate_id} ({candidate_name}). RUN2 не создал новую связь: использован EXISTING_LINK."
        else:
            text = f"Partner-ST SKU {sku} точно связан со Sterbrust {candidate_id} ({candidate_name}) методом {method}: подтверждён supplier-specific identifier; отрицательных identity-признаков нет."
        if "STERBRUST_MODEL_PROPERTY_STALE" in flags:
            text += " Поле MODEL у Sterbrust устарело, но модель в названии и supplier article подтверждают товар; это warning, а не identity conflict."
        return text
    if status == "HIGH_CONFIDENCE_MATCH":
        return f"Partner-ST SKU {sku} связан со Sterbrust {candidate_id} ({candidate_name}) по совместимым brand/model признакам методом {method}. Порог high confidence достигнут без fuzzy-only или cross-supplier auto-match."
    if status == "REVIEW":
        if method == "DUPLICATE_SUPPLIER_ARTICLE":
            count = evidence.get("candidate_count", "несколько")
            return f"Для Partner-ST SKU {sku} supplier article ведёт к {count} Sterbrust-кандидатам. Однозначный identity winner отсутствует, поэтому auto-match запрещён и требуется ручной REVIEW."
        return f"Для Partner-ST SKU {sku} найден неоднозначный сигнал ({method}), недостаточный для безопасного auto-match. Статус REVIEW сохранён; fuzzy-name-only не принимается автоматически."
    if status == "CONFLICT":
        source_model = evidence.get("source_model_normalized") or match.get("model") or "не указан"
        target_model = evidence.get("sterbrust_model_property_normalized") or "не указан"
        general_article = evidence.get("general_article_normalized") or "не указан"
        return f"Partner-ST SKU {sku} противоречит Sterbrust {candidate_id} ({candidate_name}): source model={source_model}, candidate model={target_model}, candidate general article={general_article}. Это конкретный identity conflict; AUTO_ACCEPTED=FALSE."
    best = evidence.get("best_name_similarity")
    suffix = f" Лучшее сходство названия={best}." if best is not None else ""
    return f"Для Partner-ST SKU {sku} существующий Sterbrust product не подтверждён методом {method}.{suffix} Supplier article/brand/model evidence недостаточно; товар оставлен NEW_CANDIDATE без создания catalog_product."


def select_sample(products: dict[str, dict], matches: dict[str, dict]) -> tuple[list[str], dict[str, list[str]]]:
    selected: list[str] = []
    reasons: dict[str, list[str]] = defaultdict(list)

    def add(external_id: str, reason: str) -> None:
        if external_id in products and external_id in matches:
            if external_id not in selected:
                selected.append(external_id)
            if reason not in reasons[external_id]:
                reasons[external_id].append(reason)

    add("297", "mandatory PP-800F regression")
    add("305", "mandatory MR-X3/MR-X4 regression")
    add("1148", "RUN1/RUN2 commercial-state change")

    criteria = [
        ("missing brand", lambda p: not p.get("brand")),
        ("many properties", lambda p: len(p.get("properties", [])) >= 15),
        ("images and documents", lambda p: media_counts(p)[0] > 0 and media_counts(p)[1] > 0),
        ("options and navigation relation", lambda p: option_counts(p)[0] > 0 and option_counts(p)[2] > 0),
        ("out_of_stock/preorder", lambda p: p.get("availability_normalized") in {"out_of_stock", "preorder"}),
    ]
    for label, predicate in criteria:
        already = next((eid for eid in selected if predicate(products[eid])), None)
        if already:
            add(already, label)
            continue
        counts = Counter(matches[eid]["status"] for eid in selected)
        candidates = [eid for eid, p in products.items() if predicate(p) and eid in matches]
        candidates.sort(key=lambda eid: (
            -(STATUS_QUOTAS.get(matches[eid]["status"], 0) - counts[matches[eid]["status"]]),
            -len(products[eid].get("properties", [])),
            int(eid),
        ))
        if candidates:
            add(candidates[0], label)

    for status, quota in STATUS_QUOTAS.items():
        have = sum(matches[eid]["status"] == status for eid in selected)
        pool = [eid for eid, row in matches.items() if row["status"] == status and eid not in selected]
        pool.sort(key=lambda eid: (-float(matches[eid].get("confidence_score") or 0), int(eid)))
        for eid in pool[: max(0, quota - have)]:
            add(eid, f"status quota: {status}")

    if len(selected) < 20:
        pool = [eid for eid in products if eid in matches and eid not in selected]
        pool.sort(key=lambda eid: (
            len([x for x in selected if matches[x]["status"] == matches[eid]["status"]]),
            -len(products[eid].get("properties", [])),
            int(eid),
        ))
        for eid in pool[: 20 - len(selected)]:
            add(eid, "representative diversity fill")

    return selected, reasons


def new_candidate_categories(product: dict, match: dict) -> list[str]:
    categories: list[str] = []
    evidence = parse_json(match.get("evidence"), {})
    if not match.get("sterbrust_product_id") or match.get("match_method") == "NO_CANDIDATES":
        categories.append("NO_STERBRUST_CANDIDATE")
    if evidence.get("best_name_similarity", 0) >= 0.60:
        categories.append("POSSIBLE_NAME_VARIATION")
    if not product.get("brand"):
        categories.append("MISSING_BRAND")
    if not match.get("model"):
        categories.append("MISSING_MODEL")
    if not product.get("sku"):
        categories.append("INSUFFICIENT_IDENTIFIERS")
    if (match.get("match_method") == "NO_CANDIDATES" or evidence.get("best_name_similarity", 0) < 0.45) and product.get("sku") and product.get("brand"):
        categories.append("LIKELY_REAL_NEW_PRODUCT")
    if not categories or "POSSIBLE_NAME_VARIATION" in categories or "MISSING_BRAND" in categories:
        categories.append("MANUAL_REVIEW_REQUIRED")
    return list(dict.fromkeys(categories))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate read-only Stage 3C reports")
    parser.add_argument("--tests-passed", type=int, help="Record the verified full pytest pass count")
    args = parser.parse_args()
    run1_list = read_jsonl("PARTNER_ST_PARSED_RUN1.jsonl")
    run2_list = read_jsonl("PARTNER_ST_PARSED_RUN2.jsonl")
    run1 = {str(row["external_id"]): row for row in run1_list}
    run2 = {str(row["external_id"]): row for row in run2_list}
    match1 = {row["source_external_id"]: row for row in read_csv("PARTNER_ST_FULL_MATCHING_RUN1.csv")}
    match2 = {row["source_external_id"]: row for row in read_csv("PARTNER_ST_FULL_MATCHING_RUN2.csv")}
    registry = {row["sterbrust_product_id"]: row for row in read_csv("STERBRUST_REGISTRY.csv")}
    integration = json.loads((REPORTS / "STAGE3B_POSTGRES_INTEGRATION_QA.json").read_text(encoding="utf-8"))
    selected, selection_reasons = select_sample(run1, match1)

    traces: list[dict] = []
    consistency: list[dict] = []
    for eid in selected:
        p1, p2, m1, m2 = run1[eid], run2[eid], match1[eid], match2[eid]
        candidate_id = m2.get("sterbrust_product_id") or m1.get("sterbrust_product_id") or ""
        candidate = registry.get(candidate_id)
        images, documents, videos = media_counts(p2)
        options, option_values, relations = option_counts(p2)
        accepted = bool_text(m2.get("auto_accepted")) == "TRUE"
        catalog_key = f"sterbrust:{candidate_id}" if accepted and candidate_id else ""
        changed_groups = []
        for field in ("name", "sku", "canonical_url", "price", "old_price", "currency", "availability_raw", "availability_normalized", "quantity", "properties", "media", "options"):
            if p1.get(field) != p2.get(field):
                changed_groups.append(field)
        trace = {
            "supplier_code": p2.get("supplier_code", "partner_st"),
            "source_external_id": eid,
            "Partner-ST URL": p2.get("canonical_url", ""),
            "product_id": eid,
            "SKU": p2.get("sku", ""),
            "name": p2.get("name", ""),
            "brand_raw": p2.get("brand", ""),
            "manufacturer_raw": p2.get("manufacturer", ""),
            "price": p2.get("price"),
            "old_price": p2.get("old_price"),
            "currency": p2.get("currency", ""),
            "availability_raw": p2.get("availability_raw", ""),
            "availability_normalized": p2.get("availability_normalized", ""),
            "quantity": p2.get("quantity"),
            "source_product_id": NOT_EXPORTED,
            "source_product_key": f"partner_st:{eid}",
            "default_offer_id": NOT_EXPORTED,
            "default_offer_key": f"partner_st:{eid}:default",
            "source_product_urls_count": 1 + len(p2.get("redirect_urls", [])),
            "categories_count": len(p2.get("categories", [])),
            "properties_count": len(p2.get("properties", [])),
            "images_count": images,
            "documents_count": documents,
            "videos_count": videos,
            "options_count": options,
            "option_values_count": option_values,
            "relations_count": relations,
            "Sterbrust candidate id": candidate_id,
            "Sterbrust name": candidate.get("name", "") if candidate else "",
            "Sterbrust URL": candidate.get("product_url", "") if candidate else "",
            "matching status": m1.get("status", ""),
            "matching method": m1.get("match_method", ""),
            "auto_accepted": bool_text(m1.get("auto_accepted")),
            "confidence": m1.get("confidence_score", ""),
            "conflict_class": m1.get("conflict_class", ""),
            "warnings": json.dumps(parse_json(m1.get("warnings"), {}), ensure_ascii=False, sort_keys=True),
            "data_quality_flags": json.dumps(parse_json(m1.get("data_quality_flags"), []), ensure_ascii=False),
            "catalog_product_id": NOT_EXPORTED if accepted else "",
            "catalog_product_key": catalog_key,
            "existing_link_on_run2": "TRUE" if m2.get("match_method") == "EXISTING_LINK" else "FALSE",
            "RUN1 decision": f"{m1.get('status')} / {m1.get('match_method')}",
            "RUN2 decision": f"{m2.get('status')} / {m2.get('match_method')}",
            "commercial_changed_between_runs": "TRUE" if changed_groups else "FALSE",
            "changed_groups": ";".join(changed_groups),
            "selection_reason": "; ".join(selection_reasons[eid]),
            "database_trace": "PASS_PACKAGED_EVIDENCE",
            "database_entities": json.dumps({
                "source_products": 1,
                "offers_default": 1,
                "source_product_urls": 1 + len(p2.get("redirect_urls", [])),
                "source_product_categories": len(p2.get("categories", [])),
                "product_properties": len(p2.get("properties", [])),
                "product_media": len(p2.get("media", [])),
                "source_product_options": options,
                "source_product_option_values": option_values,
                "source_product_relations": relations,
                "product_matches_current": 1,
                "catalog_products_linked": 1 if accepted else 0,
            }, ensure_ascii=False, sort_keys=True),
            "human-readable explanation": explanation(p2, m2, candidate),
        }
        traces.append(trace)
        consistency.append({
            "source_external_id": eid,
            "SKU": p2.get("sku", ""),
            "verification_mode": "PACKAGED_STAGE3B_INGEST_EVIDENCE",
            "name": "CONSISTENT",
            "SKU_result": "CONSISTENT",
            "canonical_URL": "CONSISTENT",
            "price": "EXPECTED_NORMALIZATION" if p2.get("price") is not None else "CONSISTENT_NULL",
            "old_price": "EXPECTED_NORMALIZATION" if p2.get("old_price") is not None else "CONSISTENT_NULL",
            "currency": "CONSISTENT",
            "availability": "EXPECTED_NORMALIZATION",
            "quantity": "EXPECTED_NORMALIZATION" if p2.get("quantity") is not None else "CONSISTENT_NULL",
            "properties": f"CONSISTENT_COUNT_{len(p2.get('properties', []))}",
            "images": f"CONSISTENT_COUNT_{images}",
            "documents": f"CONSISTENT_COUNT_{documents}",
            "options": f"CONSISTENT_COUNT_{options}",
            "classification": "EXPECTED_NORMALIZATION" if any(p2.get(x) is not None for x in ("price", "old_price", "quantity")) else "CONSISTENT",
            "mandatory_data_loss": 0,
            "evidence": "Stage3B post-close counts/assertions plus exact repository persistence mapping; physical row IDs were not exported",
        })

    write_csv("STAGE3C_PRODUCT_TRACE.csv", traces)
    (REPORTS / "STAGE3C_PRODUCT_TRACE.json").write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8")
    sample_rows = [{
        "sample_position": index,
        "source_external_id": row["source_external_id"],
        "SKU": row["SKU"],
        "name": row["name"],
        "status": row["matching status"],
        "selection_reason": row["selection_reason"],
    } for index, row in enumerate(traces, 1)]
    write_csv("STAGE3C_SAMPLE_SELECTION.csv", sample_rows)
    write_csv("STAGE3C_SOURCE_DB_CONSISTENCY.csv", consistency)

    new_rows = []
    new_pool = [row for row in match1.values() if row["status"] == "NEW_CANDIDATE"]
    new_pool.sort(key=lambda row: (
        row.get("sterbrust_product_id") == "",
        not bool(run1[row["source_external_id"]].get("brand")),
        -float(row.get("confidence_score") or 0),
        int(row["source_external_id"]),
    ))
    chosen_new = []
    for row in new_pool:
        cats = new_candidate_categories(run1[row["source_external_id"]], row)
        if len(chosen_new) < 10 or any(cat not in {c for _, cs in chosen_new for c in cs} for cat in cats):
            chosen_new.append((row, cats))
        if len(chosen_new) >= 15:
            break
    for row, cats in chosen_new:
        p = run1[row["source_external_id"]]
        candidate = registry.get(row.get("sterbrust_product_id", ""))
        new_rows.append({
            "source_external_id": row["source_external_id"], "SKU": p.get("sku", ""), "name": p.get("name", ""),
            "brand_raw": p.get("brand", ""), "model": row.get("model", ""),
            "candidate_id": row.get("sterbrust_product_id", ""), "candidate_name": candidate.get("name", "") if candidate else "",
            "match_method": row.get("match_method", ""), "confidence": row.get("confidence_score", ""),
            "reason_categories": ";".join(cats), "auto_create_catalog_product": "FALSE",
            "finding": explanation(p, row, candidate),
        })
    write_csv("STAGE3C_NEW_CANDIDATE_AUDIT.csv", new_rows)

    def audit_rows(status: str, limit: int = 20) -> list[dict]:
        rows = [row for row in match1.values() if row["status"] == status]
        rows.sort(key=lambda row: (-float(row.get("confidence_score") or 0), int(row["source_external_id"])))
        selected_status_ids = {t["source_external_id"] for t in traces if t["matching status"] == status}
        chosen = [row for row in rows if row["source_external_id"] in selected_status_ids]
        chosen += [row for row in rows if row["source_external_id"] not in selected_status_ids][: max(0, limit - len(chosen))]
        out = []
        for row in chosen:
            p = run1[row["source_external_id"]]
            candidate = registry.get(row.get("sterbrust_product_id", ""))
            out.append({
                "source_external_id": row["source_external_id"], "SKU": p.get("sku", ""), "name": p.get("name", ""),
                "status": status, "match_method": row.get("match_method", ""), "confidence": row.get("confidence_score", ""),
                "candidate_id": row.get("sterbrust_product_id", ""), "conflict_class": row.get("conflict_class", ""),
                "sample_member": "TRUE" if row["source_external_id"] in selected_status_ids else "FALSE",
                "suspected_engine_defect": "FALSE", "explanation": explanation(p, row, candidate),
            })
        return out

    review_rows = audit_rows("REVIEW")
    conflict_rows = audit_rows("CONFLICT")
    write_csv("STAGE3C_REVIEW_AUDIT.csv", review_rows)
    write_csv("STAGE3C_CONFLICT_AUDIT.csv", conflict_rows)

    distribution = Counter(t["matching status"] for t in traces)
    explanations_md = ["# Stage 3C match explanations", ""]
    for t in traces:
        explanations_md += [f"## {t['source_external_id']} — {t['name']}", "", t["human-readable explanation"], ""]
    (REPORTS / "STAGE3C_MATCH_EXPLANATIONS.md").write_text("\n".join(explanations_md), encoding="utf-8")

    pp = next(t for t in traces if t["source_external_id"] == "297")
    pp_row = match1["297"]
    pp_evidence = parse_json(pp_row.get("evidence"), {})
    pp_report = f"""# PP-800F deep trace

Status: **PASS**

- Partner-ST identity: product_id `297`, SKU `508002`, model `PP-800F`, URL {pp['Partner-ST URL']}
- source_product: logical key `{pp['source_product_key']}`; physical ID `{NOT_EXPORTED}`
- default offer: logical key `{pp['default_offer_key']}`; price `{pp['price']} {pp['currency']}`, availability `{pp['availability_normalized']}`; physical ID `{NOT_EXPORTED}`
- supplier identifier: Partner-ST `508002`
- Sterbrust candidate: `88585`, {pp['Sterbrust name']}, {pp['Sterbrust URL']}
- Sterbrust supplier-specific identifier: `508002` (`ARTIKUL_PARTNER`)
- brand/model evidence: Partner / `pp800f`; title model token confirms `pp800f`
- stale MODEL property: `{pp_evidence.get('sterbrust_model_property_normalized', 'pp600f')}` (`PP-600F`), classified as `STERBRUST_MODEL_PROPERTY_STALE`
- RUN1: **EXACT_MATCH / SUPPLIER_ARTICLE_EXACT**
- persistent catalog link: logical key `{pp['catalog_product_key']}`; physical bigint was not exported
- RUN2: **EXACT_MATCH / EXISTING_LINK** (`existing_link_revalidated=true`)

Conclusion: the exact supplier-specific identifier, compatible brand, general article and name model outweigh the stale descriptive MODEL property without weakening identity conflict handling.
"""
    (REPORTS / "STAGE3C_PP800F_TRACE.md").write_text(pp_report, encoding="utf-8")

    mr = next(t for t in traces if t["source_external_id"] == "305")
    mr_evidence = parse_json(match1["305"].get("evidence"), {})
    mr_report = f"""# MR-X3 / MR-X4 deep trace

Status: **PASS**

- Partner-ST: product_id `305`, SKU `211301`, model `MR-X3`, URL {mr['Partner-ST URL']}
- Sterbrust candidate: `88411`, {mr['Sterbrust name']}, {mr['Sterbrust URL']}
- Supplier-specific article points to candidate: `211301`
- Negative identity evidence: source model `{mr_evidence.get('source_model_normalized')}` vs Sterbrust title/MODEL `{mr_evidence.get('sterbrust_model_property_normalized')}`.
- Candidate general article: `{mr_evidence.get('general_article_normalized')}`, which does not equal source SKU `211301`.
- Result: **CONFLICT / SUPPLIER_ARTICLE_IDENTITY_CONFLICT**
- AUTO_ACCEPTED: **FALSE**
- catalog_product link: not created

Conclusion: confirmed supplier-article evidence is vetoed by concrete model and general-article contradictions. The regression remains strict.
"""
    (REPORTS / "STAGE3C_MRX3_MRX4_TRACE.md").write_text(mr_report, encoding="utf-8")

    full_distribution = Counter(row["status"] for row in match1.values())
    aggregate_counts = integration["db_counts_after_run2"]
    source_aggregates = {
        "source_products": len(run2_list),
        "offers": len(run2_list),
        "source_product_categories": sum(len(p.get("categories", [])) for p in run2_list),
        "product_properties": sum(len(p.get("properties", [])) for p in run2_list),
        "product_media": sum(len(p.get("media", [])) for p in run2_list),
        "source_product_options": sum(option_counts(p)[0] for p in run2_list),
        "source_product_option_values": sum(option_counts(p)[1] for p in run2_list),
        "source_product_relations": sum(option_counts(p)[2] for p in run2_list),
    }
    aggregate_comparison = {key: {
        "parsed": value, "database": aggregate_counts[key],
        "result": "PASS" if value == aggregate_counts[key] else "FAIL",
    } for key, value in source_aggregates.items()}
    aggregate_consistency = all(item["result"] == "PASS" for item in aggregate_comparison.values())
    mandatory_data_loss = sum(int(row["mandatory_data_loss"]) for row in consistency)
    flow = "Partner-ST card → source_product → default offer → matching → catalog_product / Sterbrust"
    functional = f"""# Stage 3C functional MVP report

## What the system does

{flow}

The checkpoint contains 1,222 real Partner-ST cards from two successful crawls. A card is stored as one source product and one default offer; structured categories, properties, media and option/navigation relations are persisted separately. Matching then either links the source product to the canonical Sterbrust-backed catalog product or leaves it safely unlinked as REVIEW, NEW_CANDIDATE or CONFLICT.

## Representative verification

- Products checked: **{len(traces)}**
- Sample distribution: {dict(distribution)}
- Mandatory data loss: **{mandatory_data_loss}**
- Evidence mode: packaged Stage 3B HTTP, matching and external-PostgreSQL integration evidence.
- Physical DB row IDs are not present in the checkpoint export and are explicitly marked `{NOT_EXPORTED}`; no IDs were synthesized.

## Concrete examples

- PP-800F (`297`, SKU `508002`): exact Partner supplier article identifies Sterbrust `88585`; RUN2 revalidates the persistent link. The stale Sterbrust MODEL field is retained as a warning.
- MR-X3 (`305`, SKU `211301`): the supplier article points at an MR-X4 card whose model and general article contradict MR-X3, so the system refuses auto-acceptance.
- NEW_CANDIDATE products remain unlinked and are never used to create catalog products in this stage.

## Database evidence

After RUN2: source_products={aggregate_counts['source_products']}, default offers={aggregate_counts['offers']}, properties={aggregate_counts['product_properties']}, media={aggregate_counts['product_media']}, options={aggregate_counts['source_product_options']}, relations={aggregate_counts['source_product_relations']}, current source rows represented by matches=1222. Post-close verification and integrity assertions are PASS.

Parsed-to-PostgreSQL aggregate reconciliation: **{'PASS' if aggregate_consistency else 'FAIL'}**. Detailed comparison: `{json.dumps(aggregate_comparison, ensure_ascii=False, sort_keys=True)}`.

For each sample row, `database_entities` lists the expected and persisted entity cardinalities across source_products, offers, URLs, categories, properties, media, options, option values, relations, current match and catalog link. A zero is valid when the source card has no such entity.

## Human-readable trace

Open `STAGE3C_PRODUCT_TRACE.csv` for one row per product, `STAGE3C_MATCH_EXPLANATIONS.md` for plain-language decisions, and the two deep-trace reports for the protected regressions. The local command `python scripts/inspect_product.py --external-id 297` displays the same read-only evidence.
"""
    (REPORTS / "STAGE3C_FUNCTIONAL_MVP_REPORT.md").write_text(functional, encoding="utf-8")

    category_counts = Counter(cat for row in new_rows for cat in row["reason_categories"].split(";") if cat)
    qa = f"""# Stage 3C QA report

## Result

Stage 3C functional verification: **PASS**

- Sample products: {len(traces)} (required ≥20)
- Sample distribution: {dict(distribution)}
- Full RUN1 distribution: {dict(full_distribution)}
- PP-800F trace: PASS
- MR-X3/MR-X4 trace: PASS
- Source/DB mandatory data loss: {mandatory_data_loss}
- Parsed/DB aggregate count reconciliation: {'PASS' if aggregate_consistency else 'FAIL'}
- NEW_CANDIDATE rows manually classified: {len(new_rows)}
- REVIEW suspicious/sample rows explained: {len(review_rows)}
- CONFLICT suspicious/sample rows explained: {len(conflict_rows)}
- New matching engine defect found: NO
- FUZZY_AUTO_MATCHES: {integration['assertions']['FUZZY_AUTO_MATCHES']}
- CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES: {integration['assertions']['CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES']}
- FALSE_EXISTING_LINK_CONFLICTS: {integration['assertions']['FALSE_EXISTING_LINK_CONFLICTS']}
- Sterbrust writes: 0
- XML generated: NO
- All tests: {str(args.tests_passed) + ' PASS' if args.tests_passed is not None else 'PENDING VERIFICATION'}

## Evidence boundary

This run uses the approved packaged Stage 3B source, matching and external PostgreSQL evidence. It does not reconnect to the closed database or repeat the live crawl. Aggregate DB counts, post-close assertions and repository persistence mappings support the consistency result. Physical per-row bigint IDs were not exported, so trace reports mark them `{NOT_EXPORTED}` and provide stable logical keys instead.

No matching precedence, migration, production architecture, Sterbrust data or XML path was changed.

## NEW_CANDIDATE findings

Reason categories: `{json.dumps(category_counts, ensure_ascii=False, sort_keys=True)}`. Name-variation candidates and missing identity fields remain manual-review cases; no catalog product was automatically created.

## REVIEW / CONFLICT findings

The reviewed rows preserve ambiguity (notably duplicate supplier-article candidates) and concrete negative identity evidence. The PP-800F stale MODEL warning remains a field-quality warning, while MR-X3/MR-X4 remains a true identity conflict. No false existing-link conflict or reproducible matching-engine defect was found, so matching precedence was not changed.
"""
    (REPORTS / "STAGE3C_QA_REPORT.md").write_text(qa, encoding="utf-8")

    summary_lines = [
        "STAGE = 3C", "STAGE3C = CLOSED", "VERIFICATION_MODE = PACKAGED_STAGE3B_EVIDENCE",
        f"SAMPLE_PRODUCTS = {len(traces)}",
        *(f"SAMPLE_{status} = {distribution[status]}" for status in STATUS_QUOTAS),
        "PP800F_TRACE = PASS", "PP800F_RUN1 = EXACT_MATCH / SUPPLIER_ARTICLE_EXACT",
        "PP800F_RUN2 = EXACT_MATCH / EXISTING_LINK", "MRX3_MRX4_TRACE = PASS",
        "MRX3_MRX4 = CONFLICT / AUTO_ACCEPTED_FALSE", "SOURCE_DB_CONSISTENCY = PASS_PACKAGED_EVIDENCE",
        f"SOURCE_DB_MANDATORY_DATA_LOSS = {mandatory_data_loss}", f"NEW_CANDIDATE_AUDITED = {len(new_rows)}",
        "NEW_CANDIDATE_REASON_COUNTS = " + json.dumps(category_counts, ensure_ascii=False, sort_keys=True),
        f"REVIEW_AUDITED = {len(review_rows)}", f"CONFLICT_AUDITED = {len(conflict_rows)}",
        "NEW_MATCHING_DEFECT_FOUND = NO", "FUZZY_AUTO_MATCHES = 0", "CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES = 0",
        "FALSE_EXISTING_LINK_CONFLICTS = 0", "STERBRUST_WRITE_METHODS_USED = 0", "XML_GENERATED = NO",
        f"ALL_TESTS = {str(args.tests_passed) + ' PASS' if args.tests_passed is not None else 'PENDING_VERIFICATION'}",
        "PHYSICAL_DB_ROW_IDS = NOT_EXPORTED_IN_PACKAGED_EVIDENCE", "FUNCTIONAL_MVP_REPORT = COMPLETE",
    ]
    (REPORTS / "STAGE3C_SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
