from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .models import ProductCard


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identity_value(card: ProductCard) -> dict[str, Any]:
    value = {"external_id": card.external_id, "sku": card.sku, "name": card.name,
             "brand": card.brand, "manufacturer": card.manufacturer,
             "canonical_url": card.canonical_url}
    # Preserve historical Partner-ST hashes when the new Stage 3E alias set is empty.
    if card.alias_urls:
        value["alias_urls"] = card.alias_urls
    return value


def card_hashes(card: ProductCard) -> dict[str, str]:
    return {
        "identity": stable_hash(identity_value(card)),
        "description": stable_hash({"text": card.description_text, "html": card.description_html}),
        "properties": stable_hash([asdict(item) for item in card.properties]),
        "images": stable_hash([asdict(item) for item in card.media if item.media_type == "image"]),
        "documents": stable_hash([asdict(item) for item in card.media if item.media_type in {"document", "video"}]),
        "categories": stable_hash([asdict(item) for item in card.categories]),
        "options": stable_hash([asdict(item) for item in card.options]),
        "commercial": stable_hash({"sku": card.sku, "price": card.price, "old_price": card.old_price, "currency": card.currency, "availability": card.availability_normalized, "quantity": card.quantity}),
    }


def card_group_values(card: ProductCard) -> dict[str, Any]:
    """Normalized JSON-safe values used for material change history."""
    return {
        "identity": identity_value(card),
        "description": {"text": card.description_text, "html": card.description_html},
        "properties": [asdict(item) for item in card.properties],
        "images": [asdict(item) for item in card.media if item.media_type == "image"],
        "documents": [asdict(item) for item in card.media if item.media_type in {"document", "video"}],
        "categories": [asdict(item) for item in card.categories],
        "other": {"options": [asdict(item) for item in card.options]},
        "commercial": {"sku": card.sku, "price": card.price, "old_price": card.old_price,
                       "currency": card.currency, "availability_raw": card.availability_raw,
                       "availability_normalized": card.availability_normalized, "quantity": card.quantity},
    }
