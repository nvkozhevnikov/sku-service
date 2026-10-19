"""Read-only A/B evaluation of the strict model/article identity keys.

Runs the normal ``match_cards`` pipeline twice over the same loaded data:

* ``legacy`` - the strict comparison keys are temporarily replaced by the
  legacy ``normalize_model`` key (the behaviour before the strict keys);
* ``strict`` - the current code.

Only the key functions differ between the runs, so every changed decision is
caused by the strict keys. Nothing is written to the database; callers should
open the connection with ``default_transaction_read_only=on``.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import re
from typing import Any, Iterable, Iterator

import sterbrust_matching.matching as matching_module
import sterbrust_matching.normalization as normalization_module
import sterbrust_matching.product_identity as product_identity_module
import universal_supplier.matching_service as matching_service_module
from sterbrust_matching.normalization import article_lookup_key, normalize_model

from .matching_service import RegistryContext, match_cards
from .models import ProductCard


def legacy_article(value: object) -> str:
    """The article key used before ``normalize_article``: digit groups merge."""
    return normalize_model(value)


def legacy_articles_compatible(left: object, right: object) -> bool:
    key = legacy_article(left)
    return bool(key) and key == legacy_article(right)


@contextmanager
def legacy_identity_keys() -> Iterator[None]:
    """Temporarily swap the strict comparison keys for the legacy key in every matcher module."""
    original_with_flags = normalization_module.normalize_model_with_flags

    def with_flags(value: object, brand: object = "", *, strict: bool = False):
        return original_with_flags(value, brand)

    patches = [
        (normalization_module, "normalize_model_strict", normalize_model),  # also drives model_tokens(strict=True)
        (matching_module, "normalize_model_strict", normalize_model),
        (matching_module, "normalize_model_with_flags", with_flags),
        (matching_module, "normalize_article", legacy_article),
        (matching_module, "articles_compatible", legacy_articles_compatible),
        (matching_service_module, "article_lookup_key", legacy_article),
        (product_identity_module, "normalize_article", legacy_article),
    ]
    saved = [(module, name, getattr(module, name)) for module, name, _ in patches if hasattr(module, name)]
    try:
        for module, name, replacement in patches:
            if hasattr(module, name):
                setattr(module, name, replacement)
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def rebuild_registry_articles(registry: RegistryContext, article_key) -> RegistryContext:
    """Re-key the supplier-identifier and title-article indexes with ``article_key``."""
    identifier_index: dict[tuple[str, str], list[str]] = {}
    for product_id, row in registry.products.items():
        for supplier_code, values in row["supplier_identifiers"].items():
            for raw_value in values:
                key = article_key(raw_value)
                if key:
                    identifier_index.setdefault((supplier_code, key), []).append(product_id)
    title_index: dict[tuple[str, str], list[str]] = {}
    for (supplier_code, _), product_ids in registry.title_identifier_index.items():
        for product_id in product_ids:
            for raw in re.findall(r"(?<!\d)(\d{5,10})(?!\d)", registry.products[product_id]["name"]):
                key = article_key(raw)
                if key and product_id not in title_index.setdefault((supplier_code, key), []):
                    title_index[(supplier_code, key)].append(product_id)
    return RegistryContext(registry.products, identifier_index, registry.by_model, registry.by_brand, title_index)


def key_patterns(models: Iterable[object] = (), articles: Iterable[object] = ()) -> list[str]:
    """Which strict-key rules can explain a difference for these raw model and article values."""
    found: set[str] = set()
    for kind, values in (("MODEL", models), ("ARTICLE", articles)):
        for value in values:
            text = str(value or "")
            if kind == "MODEL":
                if re.search(r"\d[.,]\d", text): found.add("MODEL_DECIMAL_POINT")
                if "+" in text: found.add("MODEL_PLUS")
            elif re.search(r"\d[\s./-]+\d", text):
                found.add("ARTICLE_DIGIT_GROUPS")
    return sorted(found)


def _decision(row: dict) -> dict:
    return {"status": row["status"], "method": row["match_method"], "candidate": row["sterbrust_product_id"],
            "auto_accepted": row["auto_accepted"] == "TRUE"}


def transition(legacy: dict, strict: dict) -> str:
    if legacy == strict:
        return "UNCHANGED"
    if legacy["auto_accepted"] and not strict["auto_accepted"]:
        return "AUTO_TO_NOT_AUTO"
    if strict["auto_accepted"] and not legacy["auto_accepted"]:
        return "NOT_AUTO_TO_AUTO"
    if legacy["candidate"] != strict["candidate"]:
        return "CANDIDATE_CHANGED"
    return "STATUS_OR_METHOD_CHANGED"


def compare(cards: Iterable[ProductCard], registry: RegistryContext,
            existing_links: dict[str, dict[str, Any]] | None = None) -> list[dict]:
    """Match the same cards with legacy and strict keys; return one comparison row per card."""
    cards = list(cards)
    with legacy_identity_keys():
        legacy_rows = match_cards(cards, rebuild_registry_articles(registry, legacy_article), existing_links)
    strict_rows = match_cards(cards, rebuild_registry_articles(registry, article_lookup_key), existing_links)
    result = []
    for card, legacy_row, strict_row in zip(cards, legacy_rows, strict_rows, strict=True):
        legacy, strict = _decision(legacy_row), _decision(strict_row)
        kind = transition(legacy, strict)
        candidate_ids = {legacy["candidate"], strict["candidate"]} - {""}
        candidates = [registry.products[key] for key in sorted(candidate_ids) if key in registry.products]
        candidate_articles = [value for row in candidates for value in
                              (row["article"], *[v for values in row["supplier_identifiers"].values() for v in values])]
        result.append({
            "supplier_code": card.supplier_code, "source_external_id": card.external_id, "sku": card.sku,
            "name": card.name, "source_model": strict_row["model"], "transition": kind,
            "legacy_status": legacy["status"], "legacy_method": legacy["method"],
            "legacy_candidate": legacy["candidate"], "legacy_auto_accepted": legacy["auto_accepted"],
            "strict_status": strict["status"], "strict_method": strict["method"],
            "strict_candidate": strict["candidate"], "strict_auto_accepted": strict["auto_accepted"],
            "candidate_models": " | ".join(row["model"] for row in candidates),
            "candidate_articles": " | ".join(row["article"] for row in candidates),
            "patterns": ",".join(key_patterns([strict_row["model"], *(row["model"] for row in candidates)],
                                              [card.sku, *candidate_articles])) if kind != "UNCHANGED" else "",
        })
    return result


def summarize(rows: list[dict]) -> dict:
    by_supplier: dict[str, Counter] = {}
    patterns: Counter[str] = Counter()
    for row in rows:
        by_supplier.setdefault(row["supplier_code"], Counter())[row["transition"]] += 1
        if row["transition"] != "UNCHANGED":
            patterns.update(filter(None, row["patterns"].split(",")))
    totals = Counter(row["transition"] for row in rows)
    return {"cards": len(rows), "transitions": dict(sorted(totals.items())),
            "by_supplier": {code: dict(sorted(counter.items())) for code, counter in sorted(by_supplier.items())},
            "changed_patterns": dict(sorted(patterns.items())),
            "legacy_auto_accepted": sum(row["legacy_auto_accepted"] for row in rows),
            "strict_auto_accepted": sum(row["strict_auto_accepted"] for row in rows)}
