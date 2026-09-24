"""Offline preparation for persisting public-price observations.

This module deliberately has no database driver, HTTP transport, scheduler,
matching, or offer-selection dependency.  It defines the payload that a later,
explicitly authorised PostgreSQL writer will persist through migration 015.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re
from typing import Any
from urllib.parse import unquote, urlparse

from .change_detection import stable_hash
from .commercial import PriceState, ReadOnlySupplierProduct
from .models import ProductCard


_AVAILABILITY = {"InStock": "in_stock", "OutOfStock": "out_of_stock", "PreOrder": "preorder"}
_SECRET_TEXT_RE = re.compile(r"(?:sessid|bitrix_sessid|cookie|authorization|access_token|api[_-]?key|token)", re.I)


def _sha256(value: str, field_name: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return value.lower()


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("capture URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("capture URL must not retain userinfo or a fragment")
    # Detail-page captures have canonical, query-free URLs.  Reject every
    # query rather than maintaining an incomplete credential denylist.
    if parsed.query or _SECRET_TEXT_RE.search(unquote(value)):
        raise ValueError("capture URL must not retain query or credential data")
    return value


def _safe_text(value: str, field_name: str) -> str:
    if _SECRET_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not retain a credential or session value")
    return value


@dataclass(frozen=True)
class HttpCapture:
    """Metadata only: raw HTML, cookies, and response headers are not stored here."""

    requested_url: str
    final_url: str
    http_status: int | None
    content_type: str | None
    observed_at: datetime
    response_sha256: str
    evidence_sha256: str
    evidence_ref: str
    redirects: tuple[str, ...] = ()
    region_code: str | None = None
    region_label: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_url", _safe_url(self.requested_url))
        object.__setattr__(self, "final_url", _safe_url(self.final_url))
        object.__setattr__(self, "response_sha256", _sha256(self.response_sha256, "response_sha256"))
        object.__setattr__(self, "evidence_sha256", _sha256(self.evidence_sha256, "evidence_sha256"))
        object.__setattr__(self, "evidence_ref", _safe_text(self.evidence_ref, "evidence_ref"))
        if not self.evidence_ref.strip():
            raise ValueError("evidence_ref is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.http_status != 200:
            raise ValueError("commercial product projection requires HTTP 200")
        if not self.content_type or not self.content_type.lower().startswith("text/html"):
            raise ValueError("commercial product projection requires text/html Content-Type")
        for redirect in self.redirects:
            _safe_url(redirect)
        for diagnostic in self.diagnostics:
            _safe_text(diagnostic, "capture diagnostics")

    @property
    def fingerprint(self) -> str:
        """Event identity: a later capture is distinct even for identical bytes."""
        return stable_hash({
            "requested_url": self.requested_url, "final_url": self.final_url,
            "http_status": self.http_status, "content_type": self.content_type,
            "observed_at": self.observed_at.isoformat(), "response_sha256": self.response_sha256,
            "evidence_sha256": self.evidence_sha256, "evidence_ref": self.evidence_ref,
            "redirects": self.redirects, "region_code": self.region_code,
            "region_label": self.region_label, "diagnostics": self.diagnostics,
        })


@dataclass(frozen=True)
class CommercialObservation:
    supplier_code: str
    external_id: str
    observed_at: datetime
    capture_sha256: str
    extraction_fingerprint: str
    price_state: PriceState
    price: Decimal | None
    old_price: Decimal | None
    currency: str | None
    availability_raw: str | None
    availability_normalized: str
    price_type: str
    price_raw: str
    price_source: str | None
    extraction_evidence: dict[str, Any]
    unusable_price_reasons: tuple[str, ...]
    region_code: str | None


@dataclass(frozen=True)
class CommercialIngestResult:
    source_product_created: bool
    offer_created: bool
    observation_created: bool
    product: ProductCard
    observation: CommercialObservation


def product_card_from_snapshot(product: ReadOnlySupplierProduct, capture: HttpCapture) -> ProductCard:
    """Translate a source snapshot without creating a catalog or identity link."""

    if capture.http_status is None:
        raise ValueError("a source_product requires a captured HTTP status; legacy transcript is insufficient")
    if not product.site_internal_id:
        raise ValueError("a stable supplier site_internal_id is required")
    extraction = product.price
    _safe_text(extraction.raw_text, "price extraction raw_text")
    for diagnostic in extraction.diagnostics:
        _safe_text(diagnostic, "price extraction diagnostics")
    price = extraction.current_price if extraction.state == PriceState.NUMERIC_PUBLIC else None
    old_price = extraction.old_price if extraction.state == PriceState.NUMERIC_PUBLIC else None
    availability = _AVAILABILITY.get(product.availability or "", "unknown")
    raw_data = {
        "commercial_capture": {
            "response_sha256": capture.response_sha256,
            "evidence_sha256": capture.evidence_sha256,
            "evidence_ref": capture.evidence_ref,
            "requested_url": capture.requested_url,
            "final_url": capture.final_url,
            "http_status": capture.http_status,
            "content_type": capture.content_type,
            "redirects": capture.redirects,
            "region_code": capture.region_code,
            "region_label": capture.region_label,
            "diagnostics": capture.diagnostics,
        },
        "price_extraction": {
            "state": extraction.state.value,
            "raw_text": extraction.raw_text,
            "source_kind": extraction.source_kind.value,
            "source_path": extraction.source_path,
            "diagnostics": extraction.diagnostics,
            "context_path": extraction.context_path,
        },
    }
    canonical_url = _safe_url(product.source_url) if product.source_url else capture.final_url
    return ProductCard(
        supplier_code=product.supplier.replace("-", "_"), external_id=product.site_internal_id,
        sku=product.manufacturer_article or "", name=product.name,
        requested_url=capture.requested_url, final_url=capture.final_url,
        canonical_url=canonical_url, http_status=capture.http_status,
        redirect_urls=capture.redirects, price=price, old_price=old_price,
        currency=extraction.currency or "", availability_raw=product.availability or "",
        availability_normalized=availability, quantity=None, description_text="", description_html="",
        brand="", manufacturer="", raw_data=raw_data,
        price_type="unknown", price_raw=extraction.raw_text,
        price_source=extraction.source_path or "",
    )


def observation_from_snapshot(product: ReadOnlySupplierProduct, capture: HttpCapture) -> CommercialObservation:
    card = product_card_from_snapshot(product, capture)
    extraction = product.price
    evidence = dict(card.raw_data["price_extraction"])
    fingerprint = stable_hash({"capture": capture.fingerprint, "product": product.site_internal_id,
                               "extraction": evidence, "availability": product.availability})
    reasons = extraction.diagnostics if extraction.state != PriceState.NUMERIC_PUBLIC else ()
    return CommercialObservation(
        supplier_code=card.supplier_code, external_id=card.external_id, observed_at=capture.observed_at,
        capture_sha256=capture.response_sha256, extraction_fingerprint=fingerprint,
        price_state=extraction.state, price=card.price, old_price=card.old_price,
        currency=extraction.currency, availability_raw=product.availability,
        availability_normalized=card.availability_normalized, price_type="unknown",
        price_raw=extraction.raw_text, price_source=extraction.source_path,
        extraction_evidence=evidence, unusable_price_reasons=tuple(reasons), region_code=capture.region_code,
    )


@dataclass
class CommercialObservationLedger:
    """Offline idempotency contract double; it is not a PostgreSQL repository."""

    products: dict[tuple[str, str], ProductCard] = field(default_factory=dict)
    offers: dict[tuple[str, str, str], ProductCard] = field(default_factory=dict)
    observations: dict[tuple[str, str, str], CommercialObservation] = field(default_factory=dict)
    projection_captures: dict[tuple[str, str], HttpCapture] = field(default_factory=dict)

    def ingest(self, product: ReadOnlySupplierProduct, capture: HttpCapture) -> CommercialIngestResult:
        card = product_card_from_snapshot(product, capture)
        observation = observation_from_snapshot(product, capture)
        product_key = (card.supplier_code, card.external_id)
        offer_key = (*product_key, "default")
        observation_key = (*product_key, observation.extraction_fingerprint)
        created_product = product_key not in self.products
        created_offer = offer_key not in self.offers
        created_observation = observation_key not in self.observations
        if created_observation:
            self.observations[observation_key] = observation
            current = self.projection_captures.get(product_key)
            same_region = current is None or (current.region_code or "") == (capture.region_code or "")
            newer_or_first = current is None or capture.observed_at >= current.observed_at
            if same_region and newer_or_first:
                self.products[product_key] = card
                self.offers[offer_key] = card
                self.projection_captures[product_key] = capture
        return CommercialIngestResult(created_product, created_offer, created_observation, card, observation)
