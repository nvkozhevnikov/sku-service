from __future__ import annotations

from dataclasses import dataclass, field

from .change_detection import card_hashes
from .models import ProductCard


@dataclass
class IngestCounts:
    new_products: int = 0
    changed_products: int = 0
    unchanged_products: int = 0
    new_offers: int = 0
    changed_offers: int = 0
    unchanged_offers: int = 0
    missing_products: int = 0
    deactivated_products: int = 0
    missing_offers: int = 0
    deactivated_offers: int = 0
    change_history_events: int = 0


@dataclass
class MemoryState:
    products: dict[tuple[str, str], dict] = field(default_factory=dict)
    offers: dict[tuple[str, str, str], dict] = field(default_factory=dict)


class InMemoryRepository:
    """Deterministic contract double; never presented as PostgreSQL integration."""

    def __init__(self, state: MemoryState | None = None) -> None:
        self.state = state or MemoryState()

    def ingest(self, cards: list[ProductCard], observed_at: str) -> IngestCounts:
        counts = IngestCounts()
        for card in cards:
            hashes = card_hashes(card)
            key = (card.supplier_code, card.external_id)
            existing = self.state.products.get(key)
            if existing is None:
                self.state.products[key] = {"first_seen_at": observed_at, "last_seen_at": observed_at, "hashes": hashes, "card": card}
                counts.new_products += 1
            elif {k: v for k, v in hashes.items() if k != "commercial"} != {k: v for k, v in existing["hashes"].items() if k != "commercial"}:
                existing.update({"last_seen_at": observed_at, "hashes": hashes, "card": card})
                counts.changed_products += 1
            else:
                existing.update({"last_seen_at": observed_at, "card": card})
                existing["hashes"]["commercial"] = hashes["commercial"]
                counts.unchanged_products += 1
            offer_key = (card.supplier_code, card.external_id, "default")
            offer = self.state.offers.get(offer_key)
            if offer is None:
                self.state.offers[offer_key] = {"first_seen_at": observed_at, "last_seen_at": observed_at, "hash": hashes["commercial"]}
                counts.new_offers += 1
            elif offer["hash"] != hashes["commercial"]:
                offer.update({"last_seen_at": observed_at, "hash": hashes["commercial"]})
                counts.changed_offers += 1
            else:
                offer["last_seen_at"] = observed_at
                counts.unchanged_offers += 1
        return counts
