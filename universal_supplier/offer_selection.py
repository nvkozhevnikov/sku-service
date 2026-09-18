"""Generic, deterministic and explainable supplier-offer selection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class OfferSelectionPolicy:
    version: str
    ranking_order: tuple[str, ...]
    allowed_offer_kinds: tuple[str, ...]
    availability_ranking: dict[str, int]
    ineligible_availability: frozenset[str]
    supplier_priority: dict[str, int]
    default_supplier_priority: int
    price_strategy: str
    unknown_price_behavior: str
    zero_price_behavior: str
    unknown_availability_behavior: str
    quantity_contradiction_behavior: str
    freshness: dict[str, Any]
    deterministic_tie_break: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OfferSelectionPolicy":
        required = {
            "version", "ranking_order", "allowed_offer_kinds", "availability_ranking",
            "ineligible_availability", "supplier_priority", "default_supplier_priority",
            "price_strategy", "unknown_price_behavior", "zero_price_behavior",
            "unknown_availability_behavior", "quantity_contradiction_behavior",
            "freshness", "deterministic_tie_break",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError("Missing offer-selection policy keys: " + ", ".join(missing))
        if not str(value["version"]).strip():
            raise ValueError("Policy version must be non-empty")
        if value["price_strategy"] != "lowest_valid_price":
            raise ValueError("Unsupported price strategy")
        return cls(
            version=str(value["version"]),
            ranking_order=tuple(value["ranking_order"]),
            allowed_offer_kinds=tuple(value["allowed_offer_kinds"]),
            availability_ranking={str(k): int(v) for k, v in value["availability_ranking"].items()},
            ineligible_availability=frozenset(map(str, value["ineligible_availability"])),
            supplier_priority={str(k): int(v) for k, v in value["supplier_priority"].items()},
            default_supplier_priority=int(value["default_supplier_priority"]),
            price_strategy=str(value["price_strategy"]),
            unknown_price_behavior=str(value["unknown_price_behavior"]),
            zero_price_behavior=str(value["zero_price_behavior"]),
            unknown_availability_behavior=str(value["unknown_availability_behavior"]),
            quantity_contradiction_behavior=str(value["quantity_contradiction_behavior"]),
            freshness=dict(value["freshness"]),
            deterministic_tie_break=tuple(value["deterministic_tie_break"]),
        )

    @classmethod
    def load(cls, path: str | Path) -> "OfferSelectionPolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def as_jsonable(self) -> dict[str, Any]:
        value = asdict(self)
        value["ineligible_availability"] = sorted(self.ineligible_availability)
        return value


@dataclass(frozen=True)
class OfferCandidate:
    catalog_product_id: int
    offer_id: int
    source_product_id: int
    supplier_id: int
    offer_supplier_id: int
    supplier_code: str
    supplier_enabled: bool
    source_catalog_product_id: int | None
    source_active: bool
    source_missed_crawls: int
    source_last_success_at: datetime | None
    offer_active: bool
    offer_kind: str
    offer_missed_crawls: int
    offer_last_success_at: datetime | None
    latest_successful_full_crawl_at: datetime | None
    price: Decimal | None
    currency: str | None
    availability: str
    availability_raw: str | None
    quantity: Decimal | None


@dataclass(frozen=True)
class SelectionDecision:
    catalog_product_id: int
    selected_offer_id: int | None
    selection_status: str
    rule_version: str
    evidence: dict[str, Any]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _freshness(candidate: OfferCandidate, policy: OfferSelectionPolicy) -> tuple[str, list[str]]:
    reasons: list[str] = []
    cfg = policy.freshness
    if cfg.get("require_missed_crawls_zero", True):
        if candidate.source_missed_crawls != 0:
            reasons.append("source_product_missed_crawls")
        if candidate.offer_missed_crawls != 0:
            reasons.append("offer_missed_crawls")
    if cfg.get("require_success_timestamps", True):
        if candidate.source_last_success_at is None:
            reasons.append("source_product_missing_last_success_at")
        if candidate.offer_last_success_at is None:
            reasons.append("offer_missing_last_success_at")
    latest = candidate.latest_successful_full_crawl_at
    if cfg.get("require_latest_successful_full_crawl", True):
        if latest is None:
            reasons.append("no_successful_full_supplier_crawl")
        else:
            if candidate.source_last_success_at is None or candidate.source_last_success_at < latest:
                reasons.append("source_product_not_confirmed_by_latest_crawl")
            if candidate.offer_last_success_at is None or candidate.offer_last_success_at < latest:
                reasons.append("offer_not_confirmed_by_latest_crawl")
    return ("current", []) if not reasons else ("stale", reasons)


def _candidate_evidence(candidate: OfferCandidate, policy: OfferSelectionPolicy) -> dict[str, Any]:
    exclusion: list[str] = []
    integrity: list[str] = []
    if candidate.source_catalog_product_id != candidate.catalog_product_id:
        integrity.append("source_product_catalog_product_mismatch")
    if candidate.offer_supplier_id != candidate.supplier_id:
        integrity.append("offer_supplier_mismatch")
    if not candidate.supplier_enabled:
        exclusion.append("supplier_disabled")
    if not candidate.source_active:
        exclusion.append("source_product_inactive")
    if not candidate.offer_active:
        exclusion.append("offer_inactive")
    if candidate.offer_kind not in policy.allowed_offer_kinds:
        exclusion.append("offer_kind_not_allowed")
    if candidate.availability in policy.ineligible_availability:
        exclusion.append("availability_not_eligible")
    if candidate.availability not in policy.availability_ranking:
        exclusion.append("availability_not_configured")
    freshness, freshness_reasons = _freshness(candidate, policy)
    if freshness != "current" and not policy.freshness.get("allow_stale_fallback", False):
        exclusion.extend(freshness_reasons)
    exclusion.extend(integrity)
    quantity_contradiction = candidate.availability == "in_stock" and candidate.quantity == 0
    price_valid = candidate.price is not None and candidate.price > 0 and bool(candidate.currency)
    return {
        "offer_id": candidate.offer_id,
        "source_product_id": candidate.source_product_id,
        "supplier_id": candidate.supplier_id,
        "supplier_code": candidate.supplier_code,
        "offer_kind": candidate.offer_kind,
        "price": _decimal(candidate.price),
        "currency": candidate.currency,
        "price_valid": price_valid,
        "price_classification": "valid" if price_valid else (
            "zero_invalid_commercial_evidence" if candidate.price == 0 else "missing_or_incomparable"
        ),
        "availability": candidate.availability,
        "availability_raw": candidate.availability_raw,
        "quantity": _decimal(candidate.quantity),
        "quantity_contradiction": quantity_contradiction,
        "freshness": freshness,
        "freshness_reasons": freshness_reasons,
        "eligible": not exclusion,
        "exclusion_reasons": exclusion,
        "integrity_errors": integrity,
        "ranking_inputs": {
            "availability_rank": policy.availability_ranking.get(candidate.availability),
            "supplier_priority": policy.supplier_priority.get(
                candidate.supplier_code, policy.default_supplier_priority
            ),
            "price_valid": price_valid,
            "price": _decimal(candidate.price),
            "currency": candidate.currency,
            "freshness": freshness,
            "tie_break": [candidate.supplier_code, candidate.offer_id],
        },
    }


def evaluate_offers(
    catalog_product_id: int,
    candidates: Iterable[OfferCandidate],
    policy: OfferSelectionPolicy,
) -> SelectionDecision:
    ordered = sorted(candidates, key=lambda item: (item.supplier_code, item.offer_id))
    rows = [_candidate_evidence(candidate, policy) for candidate in ordered]
    eligible = [row for row in rows if row["eligible"]]
    base = {
        "policy_version": policy.version,
        "ranking_order": list(policy.ranking_order),
        "catalog_product_id": catalog_product_id,
        "evaluated_candidate_count": len(rows),
        "eligible_candidate_count": len(eligible),
        "selected_offer_id": None,
        "selected_supplier_code": None,
        "selection_reason": "",
        "availability_rank": None,
        "supplier_priority": None,
        "price_rule": policy.price_strategy,
        "freshness_rule": policy.freshness,
        "candidate_offers": rows,
    }

    integrity = [error for row in rows for error in row["integrity_errors"]]
    duplicate_sources = sorted({
        (row["source_product_id"], row["offer_kind"])
        for row in rows
        if sum(1 for other in rows if (other["source_product_id"], other["offer_kind"]) ==
               (row["source_product_id"], row["offer_kind"])) > 1
    })
    if integrity:
        raise ValueError("offer selection integrity failure: " + ", ".join(sorted(set(integrity))))
    if duplicate_sources:
        base["selection_reason"] = "duplicate_offer_kind_for_source_product"
        base["integrity_review"] = {"duplicate_source_offer_kinds": [list(v) for v in duplicate_sources]}
        return SelectionDecision(catalog_product_id, None, "review", policy.version, base)
    if not eligible:
        base["selection_reason"] = "no_eligible_offer"
        return SelectionDecision(catalog_product_id, None, "no_eligible_offer", policy.version, base)
    same_supplier_sources: dict[str, set[int]] = {}
    for row in eligible:
        same_supplier_sources.setdefault(row["supplier_code"], set()).add(row["source_product_id"])
    ambiguous = {supplier: sorted(source_ids) for supplier, source_ids in same_supplier_sources.items()
                 if len(source_ids) > 1}
    if ambiguous:
        base["selection_reason"] = "same_supplier_multiple_source_products_identity_ambiguity"
        base["identity_review"] = {"same_supplier_distinct_source_products": ambiguous}
        return SelectionDecision(catalog_product_id, None, "review", policy.version, base)
    if len(eligible) == 1:
        winner = eligible[0]
        if winner["quantity_contradiction"] and policy.quantity_contradiction_behavior == "review":
            base["selection_reason"] = "in_stock_quantity_zero_contradiction"
            return SelectionDecision(catalog_product_id, None, "review", policy.version, base)
        return _selected(base, winner, "single_eligible_current_offer", policy)

    best_availability = min(row["ranking_inputs"]["availability_rank"] for row in eligible)
    finalists = [row for row in eligible if row["ranking_inputs"]["availability_rank"] == best_availability]
    best_priority = max(row["ranking_inputs"]["supplier_priority"] for row in finalists)
    finalists = [row for row in finalists if row["ranking_inputs"]["supplier_priority"] == best_priority]
    if any(row["quantity_contradiction"] for row in finalists) and policy.quantity_contradiction_behavior == "review":
        base.update({"selection_reason": "top_candidate_in_stock_quantity_zero_contradiction",
                     "availability_rank": best_availability, "supplier_priority": best_priority})
        return SelectionDecision(catalog_product_id, None, "review", policy.version, base)
    if len(finalists) == 1:
        return _selected(base, finalists[0], "availability_and_supplier_priority_winner", policy)

    priced = [row for row in finalists if row["price_valid"]]
    if priced:
        currencies = {row["currency"] for row in priced}
        if len(currencies) > 1:
            base.update({"selection_reason": "top_candidate_currency_comparison_required",
                         "availability_rank": best_availability, "supplier_priority": best_priority,
                         "incomparable_currencies": sorted(currencies)})
            return SelectionDecision(catalog_product_id, None, "review", policy.version, base)
        lowest = min(Decimal(row["price"]) for row in priced)
        finalists = [row for row in priced if Decimal(row["price"]) == lowest]
        if len(finalists) == 1:
            return _selected(base, finalists[0], "lowest_valid_price_within_top_business_rank", policy)
    else:
        base.update({"selection_reason": "multiple_top_offers_without_comparable_valid_price",
                     "availability_rank": best_availability, "supplier_priority": best_priority})
        return SelectionDecision(catalog_product_id, None, "review", policy.version, base)

    winner = min(finalists, key=lambda row: (row["supplier_code"], row["offer_id"]))
    return _selected(base, winner, "deterministic_tie_break_after_equal_business_inputs", policy)


def _selected(base: dict[str, Any], winner: dict[str, Any], reason: str,
              policy: OfferSelectionPolicy) -> SelectionDecision:
    rank = winner["ranking_inputs"]
    base.update({
        "selected_offer_id": winner["offer_id"],
        "selected_supplier_code": winner["supplier_code"],
        "selection_reason": reason,
        "availability_rank": rank["availability_rank"],
        "supplier_priority": rank["supplier_priority"],
    })
    return SelectionDecision(base["catalog_product_id"], winner["offer_id"], "selected", policy.version, base)


class PostgresOfferSelectionStore:
    """Loads accepted linked candidates and writes only catalog_offer_selection."""

    def __init__(self, connection) -> None:
        self.connection = connection

    def load(self) -> dict[int, list[OfferCandidate]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT catalog_product_id FROM (
                       SELECT DISTINCT catalog_product_id FROM source_products
                       WHERE catalog_product_id IS NOT NULL
                       UNION
                       SELECT catalog_product_id FROM catalog_offer_selection
                     ) evaluated ORDER BY catalog_product_id"""
            )
            grouped = {row[0]: [] for row in cursor.fetchall()}
            cursor.execute(
                """SELECT sp.catalog_product_id,o.id,sp.id,sp.supplier_id,o.supplier_id,s.code,s.enabled,
                          sp.catalog_product_id,sp.active,sp.missed_crawls,sp.last_success_at,
                          o.active,o.offer_kind,o.missed_crawls,o.last_success_at,
                          latest.started_at,o.price,o.currency,o.availability_normalized,
                          o.availability_raw,o.quantity
                   FROM source_products sp
                   JOIN suppliers s ON s.id=sp.supplier_id
                   JOIN offers o ON o.source_product_id=sp.id
                   LEFT JOIN LATERAL (
                       SELECT cr.started_at FROM crawl_runs cr
                       WHERE cr.supplier_id=s.id AND cr.crawl_type='full' AND cr.status='succeeded'
                       ORDER BY cr.started_at DESC LIMIT 1
                   ) latest ON true
                   WHERE sp.catalog_product_id IS NOT NULL
                   ORDER BY sp.catalog_product_id,s.code,o.id"""
            )
            for row in cursor.fetchall():
                candidate = OfferCandidate(*row)
                grouped[candidate.catalog_product_id].append(candidate)
        return grouped

    def evaluate_all(self, policy: OfferSelectionPolicy) -> list[SelectionDecision]:
        return [evaluate_offers(product_id, candidates, policy)
                for product_id, candidates in self.load().items()]

    def apply(self, decisions: Iterable[SelectionDecision]) -> dict[str, int]:
        changed = unchanged = 0
        with self.connection.transaction(), self.connection.cursor() as cursor:
            for decision in decisions:
                cursor.execute(
                    """INSERT INTO catalog_offer_selection
                       (catalog_product_id,selected_offer_id,selection_status,rule_version,evidence,evaluated_at)
                       VALUES (%s,%s,%s,%s,%s::jsonb,now())
                       ON CONFLICT (catalog_product_id) DO UPDATE SET
                         selected_offer_id=EXCLUDED.selected_offer_id,
                         selection_status=EXCLUDED.selection_status,
                         rule_version=EXCLUDED.rule_version,
                         evidence=EXCLUDED.evidence,
                         evaluated_at=now()
                       WHERE catalog_offer_selection.selected_offer_id IS DISTINCT FROM EXCLUDED.selected_offer_id
                          OR catalog_offer_selection.selection_status IS DISTINCT FROM EXCLUDED.selection_status
                          OR catalog_offer_selection.rule_version IS DISTINCT FROM EXCLUDED.rule_version
                          OR catalog_offer_selection.evidence IS DISTINCT FROM EXCLUDED.evidence""",
                    (decision.catalog_product_id, decision.selected_offer_id, decision.selection_status,
                     decision.rule_version, json.dumps(decision.evidence, ensure_ascii=False, sort_keys=True)),
                )
                if cursor.rowcount:
                    changed += 1
                else:
                    unchanged += 1
        return {"changed": changed, "unchanged": unchanged}
