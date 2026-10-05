"""Deterministic, diagnostic-only existing/new/review proposal tables.

No result is a Bitrix/ESOL payload or a persisted offer selection. Source
offers stay inactive; a proposed price is a human-review candidate only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from sterbrust_matching.normalization import normalize_model


COMMERCIAL_SOURCES = frozenset({"intervesp", "beka_mak"})
CAPTURE_REVIEW_STATUSES = frozenset({"REVIEW", "AMBIGUOUS", "BLOCKED", "UNSAFE_URL",
                                     "NETWORK_ERROR", "HTTP_STATUS", "DB_ERROR"})
AVAILABILITY_RANK = {"in_stock": 0, "incoming": 1, "preorder": 2,
                     "backorder": 2, "unknown": 3, "out_of_stock": 4}
EXACT_SECTION_BY_SOURCE_CATEGORY = {
    "ленточнопильные станки по металлу": 4004,
    "ручные ленточнопильные станки": 4005,
    "полуавтоматические ленточнопильные станки": 4037,
    "автоматические ленточнопильные станки": 4074,
    "вертикальные ленточнопильные станки": 4103,
}


def new_candidate_id(source: str, external_id: str, model_key: str) -> str:
    """Stable supplier-scoped proposal key; never a Sterbrust Product ID."""
    if source not in {"partner_st", "optimum", "intervesp", "beka_mak", "beka_mak_tr", "kami"} or not external_id or not model_key:
        raise ValueError("new candidate requires proven supplier, external ID and full model")
    identity = json.dumps([source, external_id, model_key], ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    return "NEWC-" + hashlib.sha256(identity).hexdigest()[:24].upper()


def _price(row: dict[str, Any]) -> Decimal | None:
    if row.get("source") not in COMMERCIAL_SOURCES or row.get("price_state") != "numeric_public":
        return None
    if row.get("currency") != "RUB":
        return None
    try:
        price = Decimal(str(row.get("price")))
    except (InvalidOperation, TypeError):
        return None
    return price if price > 0 else None


def _observed(row: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("observed_at") or ""))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def _source_offer(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("source", "external_id", "offer_id", "source_url",
                                            "model", "price_state", "price", "currency",
                                            "availability", "observed_at")}


def capture_review_rows(*, allowed_urls: set[tuple[str, str]],
                        discovery_reviews: list[dict], reports: list[dict]) -> list[dict[str, Any]]:
    """Preserve unresolved card/discovery evidence without promoting it to a product.

    Later reports supersede earlier statuses for the same pinned candidate URL.
    A successful later capture removes a former page-level REVIEW, but never
    deletes its historical report or sanitized evidence.
    """
    latest: dict[tuple[str, str], dict] = {}
    for report in reports:
        for row in report.get("rows", []):
            key = (str(row.get("source") or ""), str(row.get("url") or ""))
            if key in allowed_urls:
                latest[key] = row
    results = []
    for (source, url), row in sorted(latest.items()):
        status = str(row.get("status") or "")
        if status not in CAPTURE_REVIEW_STATUSES:
            continue
        results.append({"source": source, "external_id": "", "model": row.get("expected_model") or "",
                        "name": "", "source_url": url, "classification": status,
                        "export_status": "REVIEW", "export_reason": f"capture_{status.lower()}",
                        "match_status": "NOT_MATCHED_UNCONFIRMED_CARD",
                        "match_conflicts": list(row.get("diagnostics") or []),
                        "evidence_ref": row.get("evidence_ref") or "",
                        "http_status": row.get("http_status")})
    for row in discovery_reviews:
        source, url = str(row.get("source") or ""), str(row.get("url") or "")
        if not source or not url:
            continue
        results.append({"source": source, "external_id": "", "model": "", "name": "",
                        "source_url": url, "classification": "DISCOVERY_REVIEW",
                        "export_status": "REVIEW", "export_reason": str(row.get("reason") or "discovery_review"),
                        "match_status": "NOT_MATCHED_UNSTABLE_DISCOVERY",
                        "match_conflicts": [], "evidence_ref": "sitemap://pinned-manifest"})
    return sorted(results, key=lambda row: (row["source"], row["source_url"]))


def _section(section_id: int, sections: dict[int, dict[str, Any]]) -> tuple[int, str] | None:
    path: list[str] = []
    current = section_id
    seen: set[int] = set()
    while current and current not in seen:
        seen.add(current)
        item = sections.get(current)
        if not item or item.get("active") != "Y":
            return None
        path.append(str(item.get("name") or ""))
        current = int(item.get("iblockSectionId") or 0)
    if current or not path or not all(path):
        return None
    return section_id, " / ".join(reversed(path))


def build_proposal_tables(rows: list[dict[str, Any]], registry_products: dict[str, dict],
                          *, registry_model_keys: set[str] | None = None,
                          sections: dict[int, dict[str, Any]] | None = None,
                          freshness_after: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """Only an already confirmed full-model match can propose an update.

    A NEW_CANDIDATE remains review until both canonical absence and an exact
    active Sterbrust section have independent evidence. This function does not
    infer sections from model prefixes or supplier-site IDs.
    """
    by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_targets: dict[str, set[str]] = defaultdict(set)
    review: list[dict[str, Any]] = []
    new_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("model_key") or normalize_model(str(row.get("model") or "")))
        classification = row.get("classification")
        target = str(row.get("sterbrust_product_id") or "")
        if classification == "EXISTING_CONFIRMED" and key and target in registry_products:
            by_identity[target].append(row)
            model_targets[key].add(target)
        elif classification == "NEW_CANDIDATE" and key:
            new_candidates[key].append(row)
        else:
            review.append({**row, "export_status": "REVIEW" if classification == "NEW_CANDIDATE" else classification,
                           "export_reason": ("canonical_absence_and_section_not_independently_confirmed"
                                             if classification == "NEW_CANDIDATE" else "matching_review_or_conflict")})

    conflicting_keys = {key for key, targets in model_targets.items() if len(targets) > 1}
    existing: list[dict[str, Any]] = []
    for target_id, offers in sorted(by_identity.items(), key=lambda pair: int(pair[0])):
        if (any(str(row.get("model_key")) in conflicting_keys for row in offers) or
                len({str(row.get("model_key")) for row in offers}) != 1):
            review.extend({**row, "export_status": "CONFLICT",
                           "export_reason": "model_to_canonical_identity_is_not_one_to_one"} for row in offers)
            continue
        eligible = []
        for row in offers:
            price = _price(row)
            observed = _observed(row)
            if price is None or observed is None or row.get("availability") not in AVAILABILITY_RANK:
                continue
            if freshness_after is not None and observed < freshness_after:
                continue
            eligible.append((AVAILABILITY_RANK[row["availability"]], price, -observed.timestamp(),
                             str(row["source"]), int(row["offer_id"]), row))
        if not eligible:
            review.extend({**row, "export_status": "REVIEW",
                           "export_reason": "no_fresh_comparable_numeric_commercial_offer"} for row in offers)
            continue
        winner = min(eligible)[-1]
        canonical = registry_products[target_id]
        existing.append({
            "sterbrust_product_id": target_id,
            "sterbrust_name": canonical.get("name"),
            "model": winner.get("model"),
            "model_key": winner.get("model_key"),
            "selected_source_proposed": winner.get("source"),
            "source_external_id": winner.get("external_id"),
            "source_offer_id": winner.get("offer_id"),
            "price": str(_price(winner)), "currency": "RUB",
            "availability": winner.get("availability"),
            "observed_at": winner.get("observed_at"),
            "source_url": winner.get("source_url"),
            "all_source_offers": [_source_offer(row) for row in sorted(offers, key=lambda item: (
                str(item.get("source")), str(item.get("external_id"))))],
            "selection_status": "PROPOSED_ONLY_SUPPLIERS_DISABLED",
        })
    new: list[dict[str, Any]] = []
    for model_key, offers in sorted(new_candidates.items()):
        # Equal model text across independent suppliers is a candidate relation,
        # not proof that their executions are one canonical product.
        if len(offers) != 1:
            review.extend({**row, "export_status": "REVIEW",
                           "export_reason": "cross_source_new_identity_unconfirmed"}
                          for row in offers)
            continue
        source_row = offers[0]
        categories = {str(row.get("source_category") or "").strip().casefold() for row in offers}
        section_ids = {EXACT_SECTION_BY_SOURCE_CATEGORY.get(category) for category in categories}
        section = (_section(next(iter(section_ids)), sections or {}) if len(section_ids) == 1
                   and None not in section_ids else None)
        if (not source_row.get("external_id") or registry_model_keys is None or model_key in registry_model_keys or
                section is None or model_key in model_targets):
            review.extend({**row, "export_status": "REVIEW",
                           "export_reason": "canonical_absence_or_exact_section_not_confirmed"}
                          for row in offers)
            continue
        section_id, section_path = section
        new.append({"new_candidate_id": new_candidate_id(str(source_row["source"]),
                                                      str(source_row["external_id"]), model_key),
                    "source": source_row.get("source"), "external_id": source_row.get("external_id"),
                    "source_url": source_row.get("source_url"),
                    "model": source_row.get("model"), "execution": source_row.get("execution") or source_row.get("model"),
                    "model_key": model_key, "proposed_name": source_row.get("name"),
                    "sterbrust_product_id": None, "section_id": section_id,
                    "section_path": section_path,
                    "absence_evidence": "full_model_key_absent_from_active_and_inactive_GET_only_registry",
                    "source_offers": [_source_offer(row) for row in sorted(offers, key=lambda item: (
                        str(item.get("source")), str(item.get("external_id"))))]})
    review.sort(key=lambda row: (str(row.get("source")), str(row.get("external_id"))))
    return {"existing": existing, "new": new, "review": review}
