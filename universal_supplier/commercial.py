"""Pure, evidence-preserving extraction of a public product price.

The module has no PostgreSQL, crawler, scheduler, supplier, or third-party
HTML-parser dependency. An adapter supplies one saved page snapshot and an
explicit product identity; this module returns an auditable extraction claim.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from html.parser import HTMLParser
from typing import Any


class PriceState(str, Enum):
    NUMERIC_PUBLIC = "numeric_public"
    PRICE_ON_REQUEST = "price_on_request"
    MISSING = "missing"
    PARSE_ERROR = "parse_error"
    ZERO_INVALID = "zero_invalid"
    AMBIGUOUS = "ambiguous"


class PriceSourceKind(str, Enum):
    PRODUCT_PRICE_DOM = "product_price_dom"
    MICRODATA = "microdata"
    JSON_LD = "json_ld"
    NONE = "none"


@dataclass(frozen=True)
class PriceExtraction:
    """An extraction result; freshness is deliberately outside this contract."""

    state: PriceState
    current_price: Decimal | None
    old_price: Decimal | None
    currency: str | None
    raw_text: str
    source_kind: PriceSourceKind
    source_path: str | None
    diagnostics: tuple[str, ...]
    product_identity: str
    source_url: str | None
    context_path: str | None


@dataclass(frozen=True)
class ReadOnlySupplierProduct:
    """Source-derived product facts, deliberately separate from identity matching.

    ``site_internal_id`` is an identifier assigned by a supplier's site.  It is
    never promoted to a manufacturer article by this read-only boundary.
    """

    supplier: str
    source_url: str | None
    name: str
    supplier_model: str | None
    manufacturer_article: str | None
    site_internal_id: str | None
    availability: str | None
    price: PriceExtraction
    diagnostics: tuple[str, ...] = ()
    description_text: str = ""
    technical_properties: tuple[tuple[str, str], ...] = ()
    source_images: tuple[str, ...] = ()
    source_category: str = ""
    source_category_url: str | None = None
    raw_supplier_model: str | None = None
    # Optional supplier-scoped key; never pretend a URL digest is a site ID.
    supplier_external_id: str | None = None
    source_brand: str = ""
    listing_evidence: dict | None = None
    source_documents: tuple[tuple[str, str], ...] = ()
    enrichment_evidence: dict | None = None


@dataclass(frozen=True)
class _Candidate:
    value: Decimal | None
    currency: str | None
    raw_text: str
    source_kind: PriceSourceKind
    source_path: str


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)

    def text(self) -> str:
        values = list(self.fragments)
        values.extend(child.text() for child in self.children)
        return " ".join(" ".join(values).split())

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()


class _DocumentParser(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == target:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].fragments.append(data)


_REQUEST_RE = re.compile(r"(?:цен[аы]\s*по\s*запросу|узнать\s*цен[уы]|запросить\s*предложени)", re.I)
_CURRENCY_RE = re.compile(r"(?:₽|руб(?:\.|л(?:ей|я)?)?|rur|rub)", re.I)
_SPACE_RE = re.compile(r"[\s\u00a0\u202f]+")


def _currency(value: str) -> str | None:
    return "RUB" if _CURRENCY_RE.search(value or "") else None


def parse_rub_money(value: str | None) -> Decimal | None:
    """Parse a rouble amount only after the caller established price context."""

    if not value or not _currency(value):
        return None
    return _parse_number(_CURRENCY_RE.sub("", value))


def _parse_number(value: str | None) -> Decimal | None:
    """Parse a non-negative localized decimal without inferring a currency."""

    raw = (value or "").replace("−", "-").strip()
    if "-" in raw:
        return None
    raw = _SPACE_RE.sub("", raw)
    if not re.fullmatch(r"\d[\d.,]*", raw):
        return None
    if raw.count(",") and raw.count("."):
        decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
        fraction = raw.rsplit(decimal_mark, 1)[1]
        digits = raw.replace(",", "").replace(".", "")
        normalized = digits if len(fraction) not in (1, 2) else digits[:-len(fraction)] + "." + fraction
    elif raw.count(",") or raw.count("."):
        delimiter = "," if "," in raw else "."
        parts = raw.split(delimiter)
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            normalized = parts[0] + "." + parts[1]
        elif all(len(part) == 3 for part in parts[1:]):
            normalized = "".join(parts)
        else:
            return None
    else:
        normalized = raw
    try:
        result = Decimal(normalized)
    except InvalidOperation:
        return None
    return result if result >= 0 else None


def _nodes(node: _Node):
    yield node
    yield from node.descendants()


def _classes(node: _Node) -> set[str]:
    return set(node.attrs.get("class", "").lower().split())


def _context(root: _Node, identity: str) -> tuple[_Node | None, str | None]:
    keys = ("data-product-identity", "data-product-context", "data-sku", "data-product-id")
    matches = [node for node in _nodes(root) if any(node.attrs.get(key) == identity for key in keys)]
    if len(matches) == 1:
        return matches[0], "explicit_product_context"
    return (None, "multiple_product_contexts") if len(matches) > 1 else (None, None)


def _current_dom(scope: _Node) -> tuple[list[_Candidate], list[_Candidate], bool]:
    current: list[_Candidate] = []
    old: list[_Candidate] = []
    invalid = False
    for node in _nodes(scope):
        classes = _classes(node)
        raw_current = node.attrs.get("data-price-current") or node.attrs.get("data-current-price")
        if raw_current is not None or bool(classes & {"price-current", "current-price"}):
            raw = raw_current or node.text()
            candidate = _Candidate(parse_rub_money(raw), _currency(raw), raw, PriceSourceKind.PRODUCT_PRICE_DOM,
                                   "data-price-current" if raw_current is not None else "class:price-current")
            current.append(candidate)
            invalid = invalid or candidate.value is None
        raw_old = node.attrs.get("data-old-price")
        if raw_old is not None or bool(classes & {"old-price", "price-old"}) or node.tag in {"s", "del"}:
            raw = raw_old or node.text()
            old.append(_Candidate(parse_rub_money(raw), _currency(raw), raw, PriceSourceKind.PRODUCT_PRICE_DOM,
                                  "data-old-price" if raw_old is not None else "old-price-marker"))
    return current, old, invalid


def _microdata(scope: _Node) -> list[_Candidate]:
    currency = next((node.attrs.get("content") or node.attrs.get("value") or node.text()
                     for node in _nodes(scope) if node.attrs.get("itemprop", "").lower() == "pricecurrency"), "").upper()
    candidates: list[_Candidate] = []
    for node in _nodes(scope):
        if node.attrs.get("itemprop", "").lower() == "price":
            raw = node.attrs.get("content") or node.attrs.get("value") or node.text()
            candidates.append(_Candidate(_parse_number(raw), currency or None,
                                         raw, PriceSourceKind.MICRODATA, "itemprop:price"))
    return candidates


def _identity_matches(value: Any, identity: str) -> bool:
    wanted = re.sub(r"[^a-z0-9]+", "", identity.lower())
    for item in value if isinstance(value, list) else [value]:
        actual = re.sub(r"[^a-z0-9]+", "", str(item or "").lower())
        # Separator/case normalization is safe; a substring match is not.
        # BMS-230DG and BMS-230DG-WP2 are distinct executions.
        if wanted and wanted == actual:
            return True
    return False


def _records(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _records(item)
    elif isinstance(value, dict):
        if isinstance(value.get("@graph"), list):
            yield from _records(value["@graph"])
        yield value


def _json_ld(root: _Node, identity: str) -> tuple[list[_Candidate], tuple[str, ...]]:
    candidates: list[_Candidate] = []
    diagnostics: list[str] = []
    scripts = [node for node in _nodes(root) if node.tag == "script" and node.attrs.get("type", "").lower() == "application/ld+json"]
    for index, node in enumerate(scripts):
        try:
            payload = json.loads("".join(node.fragments))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        product_records = 0
        for record in _records(payload):
            kinds = record.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if "Product" not in kinds:
                continue
            product_records += 1
            if not _identity_matches([record.get("sku"), record.get("mpn"), record.get("name")], identity):
                continue
            offers = record.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                raw = str(offer.get("price") or "")
                currency = str(offer.get("priceCurrency") or "").upper() or None
                candidates.append(_Candidate(_parse_number(raw), currency, raw,
                                             PriceSourceKind.JSON_LD, f"json_ld_product[{index}]"))
        if product_records and not candidates:
            diagnostics.append("json_ld_product_identity_mismatch")
    return candidates, tuple(sorted(set(diagnostics)))


def _values(candidates: list[_Candidate]) -> set[Decimal]:
    return {candidate.value for candidate in candidates if candidate.value is not None}


def extract_public_price(source: str, *, product_identity: str, source_url: str | None = None) -> PriceExtraction:
    """Extract current public RUB price, fail-closed against adjacent products."""

    if not product_identity or not product_identity.strip():
        raise ValueError("product_identity is required to prevent cross-card extraction")
    parser = _DocumentParser()
    try:
        parser.feed(source)
        parser.close()
    except (TypeError, ValueError) as error:
        return PriceExtraction(PriceState.PARSE_ERROR, None, None, None, "", PriceSourceKind.NONE, None,
                               (f"document_parse_error:{type(error).__name__}",), product_identity, source_url, None)
    scope, context_path = _context(parser.root, product_identity)
    json_ld, json_diagnostics = _json_ld(parser.root, product_identity)
    if scope is None and context_path == "multiple_product_contexts":
        return PriceExtraction(PriceState.AMBIGUOUS, None, None, None, "", PriceSourceKind.NONE, None,
                               ("multiple_product_contexts",), product_identity, source_url, context_path)
    if scope is None and not json_ld:
        return PriceExtraction(PriceState.PARSE_ERROR, None, None, None, "", PriceSourceKind.NONE, None,
                               tuple(json_diagnostics + ("product_context_not_found",)), product_identity,
                               source_url, None)
    dom, old, invalid = _current_dom(scope) if scope is not None else ([], [], False)
    current = dom + (_microdata(scope) if scope is not None else []) + json_ld
    source_diagnostics = list(json_diagnostics)
    valid = [item for item in current if item.value is not None and item.value > 0 and item.currency == "RUB"]
    invalid_current = [item for item in current if item.value is None]
    foreign_currency = [item for item in current if item.value is not None and item.currency not in {None, "RUB"}]
    values = _values(valid)
    if len(values) > 1:
        return PriceExtraction(PriceState.AMBIGUOUS, None, None, None, "", PriceSourceKind.NONE, None,
                               tuple(source_diagnostics + ["conflicting_current_price_values"]), product_identity,
                               source_url, context_path)
    if valid and (invalid_current or foreign_currency):
        reasons = source_diagnostics + [
            *(f"unparseable_current_source:{item.source_kind.value}" for item in invalid_current),
            *(f"currency_conflict:{item.source_kind.value}={item.currency}" for item in foreign_currency),
        ]
        return PriceExtraction(PriceState.AMBIGUOUS, None, None, None, "", PriceSourceKind.NONE, None,
                               tuple(reasons), product_identity, source_url, context_path)
    if values:
        candidate = next(item for item in valid if item.value in values)
        if candidate.value == 0:
            return PriceExtraction(PriceState.ZERO_INVALID, None, None, candidate.currency, candidate.raw_text,
                                   candidate.source_kind, candidate.source_path, ("zero_price",), product_identity,
                                   source_url, context_path)
        old_values = _values(old)
        diagnostics = tuple(source_diagnostics)
        if len(valid) > 1:
            diagnostics += ("corroborated_current_price_sources",)
        if len(old_values) > 1:
            diagnostics += ("conflicting_old_price_sources",)
        if scope is not None and _REQUEST_RE.search(scope.text()):
            diagnostics += ("request_marker_coexists_with_numeric_price",)
        return PriceExtraction(PriceState.NUMERIC_PUBLIC, candidate.value,
                               next(iter(old_values)) if len(old_values) == 1 else None, candidate.currency,
                               candidate.raw_text, candidate.source_kind, candidate.source_path, diagnostics,
                               product_identity, source_url, context_path)
    zero = next((item for item in current if item.value == 0 and item.currency == "RUB"), None)
    if zero is not None:
        return PriceExtraction(PriceState.ZERO_INVALID, None, None, zero.currency, zero.raw_text, zero.source_kind,
                               zero.source_path, ("zero_price",), product_identity, source_url, context_path)
    if scope is not None and _REQUEST_RE.search(scope.text()):
        return PriceExtraction(PriceState.PRICE_ON_REQUEST, None, None, None, scope.text(),
                               PriceSourceKind.PRODUCT_PRICE_DOM, context_path, ("request_marker",),
                               product_identity, source_url, context_path)
    if invalid or any(item.raw_text for item in current):
        raw = next((item.raw_text for item in current if item.raw_text), "")
        return PriceExtraction(PriceState.PARSE_ERROR, None, None, None, raw, PriceSourceKind.PRODUCT_PRICE_DOM,
                               context_path, tuple(source_diagnostics + ["price_source_present_but_unparseable"]), product_identity,
                               source_url, context_path)
    return PriceExtraction(PriceState.MISSING, None, None, None, "", PriceSourceKind.NONE, None,
                           tuple(source_diagnostics + ["no_price_source"]), product_identity, source_url, context_path)
