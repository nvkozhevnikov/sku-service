"""Read-only, fail-closed matching against the GET-only Sterbrust registry.

These proposals never create canonical links or activate passive offers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from sterbrust_matching.normalization import model_tokens, normalize_model

from .matching_service import RegistryContext, match_cards
from .models import CategoryNode, ProductCard, PropertyValue
from .proposal_export import new_candidate_id
from .execution_evidence import axis_execution_guard, condition_execution_guard


NEW_SOURCES = frozenset({"intervesp", "beka_mak", "beka_mak_tr"})


def _card(record: dict[str, Any]) -> ProductCard:
    raw = record.get("raw_data") or {}
    content = raw.get("source_content") or {}
    identity = raw.get("source_identity") or {}
    category = str(content.get("source_category") or "")
    model = str(identity.get("raw_model") or record.get("expected_model") or record.get("sku") or "")
    categories = (CategoryNode(category, str(content.get("source_category_url") or ""), 0),) if category else ()
    return ProductCard(
        supplier_code=str(record["supplier_code"]), external_id=str(record["external_id"]),
        sku=str(record.get("sku") or ""), name=str(record.get("name") or model),
        requested_url=str(record.get("source_url") or ""), final_url=str(record.get("source_url") or ""),
        canonical_url=str(record.get("canonical_url") or record.get("source_url") or ""),
        http_status=int(record.get("last_http_status") or 200), redirect_urls=(),
        price=Decimal(str(record["price"])) if record.get("price") is not None else None,
        old_price=None, currency=str(record.get("currency") or ""),
        availability_raw=str(record.get("availability_raw") or ""),
        availability_normalized=str(record.get("availability_normalized") or "unknown"),
        quantity=None, description_text=str(content.get("description_text") or record.get("description_text") or ""),
        description_html="", brand=(str(identity.get("brand") or "") if identity.get("identity_contract")
                                    else "Beka-Mak"), manufacturer="",
        properties=tuple(PropertyValue(str(name), str(value)) for name, value in content.get("technical_properties", [])),
        categories=categories, raw_data={"source_model": model},
    )


def classify_persisted_sources(records: list[dict[str, Any]], registry: RegistryContext) -> list[dict[str, Any]]:
    """A missing old match is never sufficient proof of a new product."""
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.get("supplier_code") not in NEW_SOURCES:
            continue
        card = _card(record)
        result = match_cards([card], registry)[0]
        status = result["status"]
        model = str((record.get("raw_data") or {}).get("source_identity", {}).get("raw_model")
                    or record.get("expected_model") or result.get("model") or card.sku)
        model_key = normalize_model(model)
        proposed_id = result["sterbrust_product_id"]
        target = registry.products.get(proposed_id)
        target_model_keys = ({normalize_model(target.get("model") or "")}
                             | set(model_tokens(target.get("name") or ""))) if target else set()
        full_model_confirmed = bool(model_key and model_key in target_model_keys)
        if (status in {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"}
                and result["auto_accepted"] == "TRUE" and full_model_confirmed):
            classification = "EXISTING_CONFIRMED"
            sterbrust_id = proposed_id
        elif status in {"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"} and not full_model_confirmed:
            classification = "REVIEW"
            sterbrust_id = ""
        elif status == "CONFLICT":
            classification = "CONFLICT"
            sterbrust_id = ""
        elif status == "NEW_CANDIDATE":
            classification = "NEW_CANDIDATE"
            sterbrust_id = ""
        else:
            classification = "REVIEW"
            sterbrust_id = ""
        axis = axis_execution_guard(card.name, {p.name:p.value for p in card.properties},
                                    target.get('name','') if target else '', target.get('properties',{}) if target else {})
        if classification == 'EXISTING_CONFIRMED' and axis['blocked']:
            classification = 'CONFLICT' if axis.get('contradiction') else 'REVIEW'
            sterbrust_id = ''
        condition = condition_execution_guard(card.name, {p.name:p.value for p in card.properties},
            target.get('name','') if target else '', target.get('properties',{}) if target else {})
        if classification == 'EXISTING_CONFIRMED' and condition['blocked']:
            classification = 'CONFLICT' if condition['contradiction'] else 'REVIEW'
            sterbrust_id = ''
        rows.append({
            "source": card.supplier_code, "external_id": card.external_id,
            "source_product_id": int(record["source_product_id"]),
            "offer_id": int(record["offer_id"]), "source_url": card.canonical_url,
            "name": card.name, "model": model, "execution": model, "model_key": model_key,
            "new_candidate_id": (new_candidate_id(card.supplier_code, card.external_id, model_key)
                                 if classification == "NEW_CANDIDATE" and model_key else ""),
            "source_category": (card.categories[0].name if card.categories else ""),
            "classification": classification, "sterbrust_product_id": sterbrust_id,
            "proposed_sterbrust_id": result["sterbrust_product_id"] if classification != "EXISTING_CONFIRMED" else "",
            "match_status": status, "match_method": result["match_method"],
            "full_model_confirmed": full_model_confirmed,
            "target_model_keys": sorted(target_model_keys),
            "match_conflicts": result["conflicts"], "match_warnings": result["warnings"],
            "axis_execution_evidence": axis,
            "condition_execution_evidence": condition,
            "price_state": str(record.get("price_state") or ""),
            "price": str(record["price"]) if record.get("price") is not None else None,
            "currency": record.get("currency"),
            "availability": card.availability_normalized,
            "observed_at": str(record.get("observed_at") or ""),
        })
    return rows


def proposal_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source = defaultdict(Counter)
    model_sources = defaultdict(set)
    for row in rows:
        by_source[row["source"]][row["classification"]] += 1
        if row["model_key"]:
            model_sources[row["model_key"]].add(row["source"])
    return {
        "by_source": {source: dict(counts) for source, counts in sorted(by_source.items())},
        "unique_exact_model_keys": len(model_sources),
        "cross_source_overlap": sum(len(sources) > 1 for sources in model_sources.values()),
        "selected_offers": 0,
        "selection_note": "Passive suppliers/offers remain disabled; policy v1 has no eligible offer.",
    }
