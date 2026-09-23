"""Deterministic Stage 5 YML feed generation from persisted selection state."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse


ACCEPTED_MATCH_STATUSES = frozenset({"EXACT_MATCH", "HIGH_CONFIDENCE_MATCH"})
KNOWN_AVAILABILITY_STATUSES = frozenset({
    "in_stock", "out_of_stock", "preorder", "incoming",
    "backorder", "unknown", "discontinued",
})


class FeedGenerationError(ValueError):
    """A fail-closed feed error with machine-readable violations."""

    def __init__(self, violations: Iterable[str]):
        self.violations = tuple(str(item) for item in violations)
        super().__init__("; ".join(self.violations))


@dataclass(frozen=True)
class FeedPolicy:
    version: str
    feed_type: str
    shop_name: str
    shop_company: str
    shop_url: str
    shop_metadata_source: str
    currencies: dict[str, str]
    availability_mapping: dict[str, bool]
    availability_rationale: dict[str, str]
    include_quantity_param: bool
    quantity_param_name: str
    external_offer_id_source: str
    snapshot_timestamp_source: str
    category_hierarchy: str
    unsupported_currency_behavior: str
    invalid_selected_row_behavior: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FeedPolicy":
        required = {
            "version", "feed_type", "shop", "currencies", "availability_mapping",
            "availability_rationale", "include_quantity_param", "quantity_param_name",
            "external_offer_id_source", "snapshot_timestamp_source", "category_hierarchy",
            "unsupported_currency_behavior", "invalid_selected_row_behavior",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError("Missing feed-policy keys: " + ", ".join(missing))
        shop = value["shop"]
        for key in ("name", "company", "url", "metadata_source"):
            if not str(shop.get(key, "")).strip():
                raise ValueError(f"Feed shop.{key} must be non-empty")
        parsed_url = urlparse(str(shop["url"]))
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Feed shop.url must be absolute HTTP(S)")
        currencies = {str(k): str(v) for k, v in value["currencies"].items()}
        if not currencies:
            raise ValueError("At least one feed currency is required")
        for currency_id, rate in currencies.items():
            if not re.fullmatch(r"[A-Z]{3}", currency_id):
                raise ValueError(f"Invalid currency ID: {currency_id}")
            try:
                if Decimal(rate) <= 0:
                    raise ValueError
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Invalid currency rate for {currency_id}") from exc
        availability = {str(k): bool(v) for k, v in value["availability_mapping"].items()}
        missing_availability = sorted(KNOWN_AVAILABILITY_STATUSES - availability.keys())
        if missing_availability:
            raise ValueError("Missing availability mappings: " + ", ".join(missing_availability))
        if value["external_offer_id_source"] != "sterbrust_product_id":
            raise ValueError("Stage 5 external offer ID must be sterbrust_product_id")
        return cls(
            version=str(value["version"]),
            feed_type=str(value["feed_type"]),
            shop_name=str(shop["name"]),
            shop_company=str(shop["company"]),
            shop_url=str(shop["url"]),
            shop_metadata_source=str(shop["metadata_source"]),
            currencies=currencies,
            availability_mapping=availability,
            availability_rationale={str(k): str(v) for k, v in value["availability_rationale"].items()},
            include_quantity_param=bool(value["include_quantity_param"]),
            quantity_param_name=str(value["quantity_param_name"]),
            external_offer_id_source=str(value["external_offer_id_source"]),
            snapshot_timestamp_source=str(value["snapshot_timestamp_source"]),
            category_hierarchy=str(value["category_hierarchy"]),
            unsupported_currency_behavior=str(value["unsupported_currency_behavior"]),
            invalid_selected_row_behavior=str(value["invalid_selected_row_behavior"]),
        )

    @classmethod
    def load(cls, path: str | Path) -> "FeedPolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def as_jsonable(self) -> dict[str, Any]:
        value = asdict(self)
        value["shop"] = {
            "name": value.pop("shop_name"),
            "company": value.pop("shop_company"),
            "url": value.pop("shop_url"),
            "metadata_source": value.pop("shop_metadata_source"),
        }
        return value


@dataclass(frozen=True)
class FeedRow:
    catalog_product_id: int
    selection_status: str
    selected_offer_id: int | None
    selection_rule_version: str | None
    selection_reason: str | None
    selection_evaluated_at: datetime | None
    offer_id: int | None
    offer_supplier_id: int | None
    source_product_id: int | None
    source_supplier_id: int | None
    source_catalog_product_id: int | None
    supplier_code: str | None
    supplier_enabled: bool | None
    supplier_external_id: str | None
    supplier_sku: str | None
    price: Decimal | None
    old_price: Decimal | None
    currency: str | None
    availability_normalized: str | None
    availability_raw: str | None
    quantity: Decimal | None
    offer_active: bool | None
    source_active: bool | None
    sterbrust_product_id: str | None
    sterbrust_name: str | None
    sterbrust_url: str | None
    sterbrust_category_id: str | None
    sterbrust_category_name: str | None
    sterbrust_brand: str | None
    sterbrust_active: bool | None
    current_match_status: str | None
    current_match_catalog_product_id: int | None
    sterbrust_article: str | None = None
    price_type: str | None = None
    confirmed_identity_id: str | None = None


SUPPORTED_PRICE_TYPES = frozenset({"RRP", "retail", "wholesale", "dealer", "promo"})


@dataclass(frozen=True)
class FeedSnapshot:
    selected_rows: tuple[FeedRow, ...]
    not_managed_rows: tuple[dict[str, Any], ...]
    selection_counts: dict[str, int]
    snapshot_timestamp: datetime | None

    @property
    def db_selected_count(self) -> int:
        return int(self.selection_counts.get("selected", 0))


@dataclass(frozen=True)
class FeedValidationResult:
    valid: bool
    violations: tuple[str, ...]
    metrics: dict[str, Any]

    def as_jsonable(self) -> dict[str, Any]:
        return {
            "result": "PASS" if self.valid else "FAIL",
            "violation_count": len(self.violations),
            "violations": list(self.violations),
            **self.metrics,
        }


@dataclass(frozen=True)
class FeedArtifact:
    data: bytes
    sha256: str
    trace_rows: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    validation: FeedValidationResult
    manifest: dict[str, Any]


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _id_sort_key(value: str | None) -> tuple[int, int, str]:
    text = str(value or "")
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def _snapshot_date(value: datetime | None) -> str:
    if value is None:
        raise FeedGenerationError(["MISSING_DETERMINISTIC_SNAPSHOT_TIMESTAMP"])
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def selected_row_violations(rows: Iterable[FeedRow], policy: FeedPolicy,
                            db_selected_count: int) -> list[str]:
    rows = list(rows)
    violations: list[str] = []
    if len(rows) != db_selected_count:
        violations.append(f"SELECTED_JOIN_CARDINALITY:{len(rows)}!={db_selected_count}")
    catalog_counts = Counter(row.catalog_product_id for row in rows)
    offer_id_counts = Counter(str(row.sterbrust_product_id or "") for row in rows)
    for catalog_product_id, count in sorted(catalog_counts.items()):
        if count != 1:
            violations.append(f"DUPLICATE_CATALOG_PRODUCT:{catalog_product_id}:{count}")
    for feed_id, count in sorted(offer_id_counts.items()):
        if not feed_id:
            continue
        if count != 1:
            violations.append(f"DUPLICATE_FEED_OFFER_ID:{feed_id}:{count}")

    selection_versions = {row.selection_rule_version for row in rows if row.selection_rule_version}
    if len(selection_versions) > 1:
        violations.append("MIXED_SELECTION_POLICY_VERSIONS:" + ",".join(sorted(selection_versions)))
    for row in rows:
        ref = f"catalog_product={row.catalog_product_id}"
        if row.selection_status != "selected":
            violations.append(f"NOT_SELECTED:{ref}:{row.selection_status}")
        if row.selected_offer_id is None:
            violations.append(f"MISSING_SELECTED_OFFER:{ref}")
        if row.offer_id is None or row.offer_id != row.selected_offer_id:
            violations.append(f"SELECTED_OFFER_MISMATCH:{ref}")
        if row.source_product_id is None:
            violations.append(f"MISSING_SOURCE_PRODUCT:{ref}")
        if row.source_catalog_product_id != row.catalog_product_id:
            violations.append(f"SOURCE_CATALOG_PRODUCT_MISMATCH:{ref}")
        if (row.offer_supplier_id is None or row.source_supplier_id is None
                or row.offer_supplier_id != row.source_supplier_id):
            violations.append(f"SUPPLIER_RELATIONSHIP_MISMATCH:{ref}")
        if row.current_match_status not in ACCEPTED_MATCH_STATUSES:
            violations.append(f"NO_CURRENT_ACCEPTED_MATCH:{ref}:{row.current_match_status}")
        if row.current_match_catalog_product_id != row.catalog_product_id:
            violations.append(f"MATCH_CATALOG_PRODUCT_MISMATCH:{ref}")
        if row.supplier_enabled is not True:
            violations.append(f"DISABLED_OR_MISSING_SUPPLIER:{ref}")
        if row.source_active is not True:
            violations.append(f"INACTIVE_OR_MISSING_SOURCE_PRODUCT:{ref}")
        if row.offer_active is not True:
            violations.append(f"INACTIVE_OR_MISSING_SELECTED_OFFER:{ref}")
        if row.sterbrust_active is not True:
            violations.append(f"INACTIVE_OR_MISSING_STERBRUST_PRODUCT:{ref}")
        if not str(row.sterbrust_product_id or "").strip():
            violations.append(f"MISSING_STERBRUST_PRODUCT_ID:{ref}")
        if not str(row.sterbrust_name or "").strip():
            violations.append(f"MISSING_STERBRUST_NAME:{ref}")
        if not _valid_http_url(row.sterbrust_url):
            violations.append(f"INVALID_STERBRUST_URL:{ref}")
        if not str(row.sterbrust_category_id or "").strip():
            violations.append(f"MISSING_STERBRUST_CATEGORY_ID:{ref}")
        if not str(row.sterbrust_category_name or "").strip():
            violations.append(f"MISSING_STERBRUST_CATEGORY_NAME:{ref}")
        if row.price is None:
            violations.append(f"NULL_SELECTED_PRICE:{ref}")
        elif row.price <= 0:
            violations.append(f"NON_POSITIVE_SELECTED_PRICE:{ref}:{_decimal_text(row.price)}")
        if row.currency not in policy.currencies:
            violations.append(f"UNSUPPORTED_CURRENCY:{ref}:{row.currency}")
        if row.availability_normalized not in policy.availability_mapping:
            violations.append(f"UNMAPPED_AVAILABILITY:{ref}:{row.availability_normalized}")
        if row.availability_normalized == "discontinued":
            violations.append(f"DISCONTINUED_SELECTED_OFFER:{ref}")
        if not str(row.selection_rule_version or "").strip():
            violations.append(f"MISSING_SELECTION_POLICY_VERSION:{ref}")
        if row.availability_normalized == "backorder" and row.quantity is None:
            violations.append(f"BACKORDER_UNKNOWN_QUANTITY:{ref}")
        if row.price_type is not None and str(row.price_type).strip() not in SUPPORTED_PRICE_TYPES:
            violations.append(f"UNSUPPORTED_PRICE_TYPE:{ref}:{row.price_type}")
        if row.confirmed_identity_id is not None:
            confirmed = str(row.confirmed_identity_id).strip()
            current = str(row.sterbrust_product_id or "").strip()
            if not confirmed or confirmed != current:
                violations.append(f"STALE_CONFIRMED_IDENTITY:{ref}:{current or 'нет'}->{confirmed or 'нет'}")
    return violations


def _expected_offer_values(row: FeedRow, policy: FeedPolicy) -> dict[str, Any]:
    assert row.price is not None
    feed_id = str(row.sterbrust_product_id)
    result = {
        "id": feed_id,
        "available": "true" if policy.availability_mapping[str(row.availability_normalized)] else "false",
        "url": str(row.sterbrust_url),
        "price": _decimal_text(row.price),
        "currencyId": str(row.currency),
        "categoryId": str(row.sterbrust_category_id),
        "name": str(row.sterbrust_name),
        "stb_1c_bitrix_id": feed_id,
        "vendor": str(row.sterbrust_brand) if row.sterbrust_brand else None,
        "vendorCode": _vendor_code(row),
        "sterbrust_article": _sterbrust_own_article(row),
        "oldprice": _decimal_text(row.old_price) if row.old_price is not None and row.old_price > row.price else None,
        "quantity": (_decimal_text(row.quantity) if policy.include_quantity_param
                     and row.quantity is not None else None),
    }
    return result


def _clean_article(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _vendor_code(row: FeedRow) -> str | None:
    """Selected supplier offer SKU only. Never supplier external_id."""
    sku = _clean_article(row.supplier_sku)
    external_id = _clean_article(row.supplier_external_id)
    if not sku or sku == external_id:
        return None
    return sku


def _sterbrust_own_article(row: FeedRow) -> str | None:
    """Own Sterbrust article, never supplier-specific ARTIKUL_* / supplier SKU."""
    article = _clean_article(row.sterbrust_article)
    sku = _clean_article(row.supplier_sku)
    if not article or article == sku:
        return None
    lowered = article.lower()
    if lowered.startswith("artikul_") or lowered.startswith("артикул_"):
        return None
    return article


def build_yml(snapshot: FeedSnapshot, policy: FeedPolicy) -> FeedArtifact:
    rows = list(snapshot.selected_rows)
    violations = selected_row_violations(rows, policy, snapshot.db_selected_count)
    if violations:
        raise FeedGenerationError(violations)
    rows.sort(key=lambda row: _id_sort_key(row.sterbrust_product_id))
    categories: dict[str, str] = {}
    category_conflicts: list[str] = []
    for row in rows:
        category_id = str(row.sterbrust_category_id)
        category_name = str(row.sterbrust_category_name)
        previous = categories.setdefault(category_id, category_name)
        if previous != category_name:
            category_conflicts.append(
                f"CATEGORY_NAME_CONFLICT:{category_id}:{previous!r}!={category_name!r}"
            )
    if category_conflicts:
        raise FeedGenerationError(category_conflicts)

    root = ET.Element("yml_catalog", {"date": _snapshot_date(snapshot.snapshot_timestamp)})
    shop = ET.SubElement(root, "shop")
    ET.SubElement(shop, "name").text = policy.shop_name
    ET.SubElement(shop, "company").text = policy.shop_company
    ET.SubElement(shop, "url").text = policy.shop_url
    currencies = ET.SubElement(shop, "currencies")
    for currency_id in sorted(policy.currencies):
        ET.SubElement(currencies, "currency", {
            "id": currency_id,
            "rate": policy.currencies[currency_id],
        })
    category_nodes = ET.SubElement(shop, "categories")
    for category_id in sorted(categories, key=_id_sort_key):
        ET.SubElement(category_nodes, "category", {"id": category_id}).text = categories[category_id]
    offers = ET.SubElement(shop, "offers")
    trace_rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    oldprice_count = 0
    available_counts = Counter()
    for row in rows:
        expected = _expected_offer_values(row, policy)
        offer = ET.SubElement(offers, "offer", {
            "id": expected["id"], "available": expected["available"],
        })
        for tag in ("url", "price", "currencyId", "categoryId", "name", "stb_1c_bitrix_id"):
            ET.SubElement(offer, tag).text = expected[tag]
        if expected["vendor"] is not None:
            ET.SubElement(offer, "vendor").text = expected["vendor"]
        if expected["vendorCode"] is not None:
            ET.SubElement(offer, "vendorCode").text = expected["vendorCode"]
        if expected["sterbrust_article"] is not None:
            ET.SubElement(offer, "param", {"name": "Артикул Sterbrust"}).text = expected["sterbrust_article"]
        if expected["oldprice"] is not None:
            ET.SubElement(offer, "oldprice").text = expected["oldprice"]
            oldprice_count += 1
        elif row.old_price is not None and row.price is not None and row.old_price <= row.price:
            warnings.append({
                "catalog_product_id": row.catalog_product_id,
                "selected_offer_id": row.selected_offer_id,
                "warning": "OLD_PRICE_NOT_GREATER_THAN_PRICE",
                "price": _decimal_text(row.price),
                "old_price": _decimal_text(row.old_price),
            })
        if expected["quantity"] is not None:
            ET.SubElement(offer, "param", {"name": policy.quantity_param_name}).text = expected["quantity"]
        available_counts[expected["available"]] += 1
        trace_rows.append({
            "feed_offer_id": expected["id"],
            "sterbrust_product_id": row.sterbrust_product_id,
            "catalog_product_id": row.catalog_product_id,
            "sterbrust_name": row.sterbrust_name,
            "sterbrust_category_id": row.sterbrust_category_id,
            "selected_offer_id": row.selected_offer_id,
            "source_product_id": row.source_product_id,
            "supplier_code": row.supplier_code,
            "supplier_external_id": row.supplier_external_id,
            "supplier_sku": row.supplier_sku,
            "price": expected["price"],
            "old_price": _decimal_text(row.old_price) if row.old_price is not None else "",
            "currency": row.currency,
            "availability_normalized": row.availability_normalized,
            "availability_raw": row.availability_raw,
            "quantity": _decimal_text(row.quantity) if row.quantity is not None else "",
            "selection_rule_version": row.selection_rule_version,
            "selection_reason": row.selection_reason,
            "feed_available": expected["available"],
            "feed_export_result": "EXPORTED",
        })

    ET.indent(root, space="  ")
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True) + b"\n"
    validation = validate_yml_bytes(data, expected_rows=rows, policy=policy)
    if not validation.valid:
        raise FeedGenerationError(validation.violations)
    sha256 = hashlib.sha256(data).hexdigest()
    availability_counts = Counter(str(row.availability_normalized) for row in rows)
    currency_counts = Counter(str(row.currency) for row in rows)
    versions = sorted({str(row.selection_rule_version) for row in rows})
    snapshot_timestamp = snapshot.snapshot_timestamp
    if snapshot_timestamp is not None and snapshot_timestamp.tzinfo is None:
        snapshot_timestamp = snapshot_timestamp.replace(tzinfo=timezone.utc)
    manifest = {
        "file": "exports/sterbrust_selected_offers.xml",
        "sha256": sha256,
        "feed_policy_version": policy.version,
        "selection_policy_version": versions[0] if versions else None,
        "db_selected_count": snapshot.db_selected_count,
        "exported_offer_count": len(rows),
        "category_count": len(categories),
        "currency_count": len(currency_counts),
        "availability_counts": dict(sorted(availability_counts.items())),
        "available_true_count": available_counts["true"],
        "available_false_count": available_counts["false"],
        "oldprice_exported_count": oldprice_count,
        "currency_distribution": dict(sorted(currency_counts.items())),
        "feed_size_bytes": len(data),
        "generated_from_snapshot_timestamp": (
            snapshot_timestamp.astimezone(timezone.utc).isoformat()
            if snapshot_timestamp is not None else None
        ),
    }
    return FeedArtifact(
        data=data,
        sha256=sha256,
        trace_rows=tuple(trace_rows),
        warnings=tuple(warnings),
        validation=validation,
        manifest=manifest,
    )


def _single_child(parent: ET.Element, tag: str, offer_id: str,
                  violations: list[str]) -> ET.Element | None:
    nodes = parent.findall(tag)
    if len(nodes) != 1:
        violations.append(f"OFFER_FIELD_COUNT:{offer_id}:{tag}:{len(nodes)}")
        return nodes[0] if nodes else None
    return nodes[0]


def validate_yml_bytes(data: bytes, *, expected_rows: Iterable[FeedRow] | None = None,
                       policy: FeedPolicy | None = None) -> FeedValidationResult:
    violations: list[str] = []
    metrics: dict[str, Any] = {
        "xml_well_formed": False,
        "utf8": False,
        "offer_count": 0,
        "category_count": 0,
        "currency_count": 0,
        "duplicate_offer_ids": 0,
        "duplicate_category_ids": 0,
        "duplicate_currency_ids": 0,
        "dangling_category_references": 0,
        "undeclared_currencies": 0,
        "invalid_prices": 0,
    }
    try:
        text = data.decode("utf-8", errors="strict")
        metrics["utf8"] = True
    except UnicodeDecodeError as exc:
        return FeedValidationResult(False, (f"INVALID_UTF8:{exc}",), metrics)
    declaration = re.match(r"^<\?xml\s+version=['\"]1\.0['\"]\s+encoding=['\"]utf-8['\"]\s*\?>", text, re.I)
    if not declaration:
        violations.append("MISSING_OR_INVALID_UTF8_XML_DECLARATION")
    try:
        root = ET.fromstring(data)
        metrics["xml_well_formed"] = True
    except ET.ParseError as exc:
        violations.append(f"XML_PARSE_ERROR:{exc}")
        return FeedValidationResult(False, tuple(violations), metrics)
    if root.tag != "yml_catalog":
        violations.append(f"INVALID_ROOT:{root.tag}")
    if not str(root.get("date") or "").strip():
        violations.append("MISSING_YML_CATALOG_DATE")
    shops = root.findall("shop")
    if len(shops) != 1:
        violations.append(f"SHOP_COUNT:{len(shops)}")
        return FeedValidationResult(False, tuple(violations), metrics)
    shop = shops[0]
    for tag in ("name", "company", "url"):
        nodes = shop.findall(tag)
        if len(nodes) != 1 or not str(nodes[0].text if nodes else "").strip():
            violations.append(f"INVALID_SHOP_FIELD:{tag}")
    section_counts = {tag: len(shop.findall(tag)) for tag in ("currencies", "categories", "offers")}
    for tag, count in section_counts.items():
        if count != 1:
            violations.append(f"SHOP_SECTION_COUNT:{tag}:{count}")
    child_tags = [child.tag for child in list(shop)]
    try:
        currency_position = child_tags.index("currencies")
        category_position = child_tags.index("categories")
        offers_position = child_tags.index("offers")
        if not currency_position < category_position < offers_position:
            violations.append("INVALID_SHOP_SECTION_ORDER")
    except ValueError:
        pass
    if any(count != 1 for count in section_counts.values()):
        return FeedValidationResult(False, tuple(violations), metrics)
    currencies_node = shop.find("currencies")
    categories_node = shop.find("categories")
    offers_node = shop.find("offers")
    assert currencies_node is not None and categories_node is not None and offers_node is not None

    currency_ids = [str(node.get("id") or "") for node in currencies_node.findall("currency")]
    category_ids = [str(node.get("id") or "") for node in categories_node.findall("category")]
    offers = offers_node.findall("offer")
    offer_ids = [str(node.get("id") or "") for node in offers]
    metrics["currency_count"] = len(currency_ids)
    metrics["category_count"] = len(category_ids)
    metrics["offer_count"] = len(offers)
    metrics["duplicate_currency_ids"] = sum(count - 1 for count in Counter(currency_ids).values() if count > 1)
    metrics["duplicate_category_ids"] = sum(count - 1 for count in Counter(category_ids).values() if count > 1)
    metrics["duplicate_offer_ids"] = sum(count - 1 for count in Counter(offer_ids).values() if count > 1)
    if "" in currency_ids:
        violations.append("EMPTY_CURRENCY_ID")
    if "" in category_ids:
        violations.append("EMPTY_CATEGORY_ID")
    if "" in offer_ids:
        violations.append("EMPTY_OFFER_ID")
    if metrics["duplicate_currency_ids"]:
        violations.append(f"DUPLICATE_CURRENCY_IDS:{metrics['duplicate_currency_ids']}")
    if metrics["duplicate_category_ids"]:
        violations.append(f"DUPLICATE_CATEGORY_IDS:{metrics['duplicate_category_ids']}")
    if metrics["duplicate_offer_ids"]:
        violations.append(f"DUPLICATE_OFFER_IDS:{metrics['duplicate_offer_ids']}")
    declared_categories = set(category_ids)
    declared_currencies = set(currency_ids)
    parsed_offers: dict[str, dict[str, Any]] = {}
    for offer in offers:
        offer_id = str(offer.get("id") or "")
        available = str(offer.get("available") or "")
        if available not in {"true", "false"}:
            violations.append(f"INVALID_AVAILABLE:{offer_id}:{available}")
        nodes = {tag: _single_child(offer, tag, offer_id, violations)
                 for tag in ("url", "price", "currencyId", "categoryId", "name", "stb_1c_bitrix_id")}
        values = {tag: str(node.text or "") if node is not None else "" for tag, node in nodes.items()}
        if not _valid_http_url(values["url"]):
            violations.append(f"INVALID_OFFER_URL:{offer_id}")
        for tag in ("currencyId", "categoryId", "name", "stb_1c_bitrix_id"):
            if not values[tag].strip():
                violations.append(f"EMPTY_REQUIRED_OFFER_FIELD:{offer_id}:{tag}")
        if values["stb_1c_bitrix_id"] != offer_id:
            violations.append(
                f"STB_1C_BITRIX_ID_MISMATCH:{offer_id}:{values['stb_1c_bitrix_id']}"
            )
        try:
            if not values["price"] or "," in values["price"] or Decimal(values["price"]) <= 0:
                raise InvalidOperation
            price = Decimal(values["price"])
        except (InvalidOperation, ValueError):
            metrics["invalid_prices"] += 1
            violations.append(f"INVALID_PRICE:{offer_id}:{values['price']}")
            price = None
        if values["categoryId"] not in declared_categories:
            metrics["dangling_category_references"] += 1
            violations.append(f"DANGLING_CATEGORY:{offer_id}:{values['categoryId']}")
        if values["currencyId"] not in declared_currencies:
            metrics["undeclared_currencies"] += 1
            violations.append(f"UNDECLARED_CURRENCY:{offer_id}:{values['currencyId']}")
        oldprices = offer.findall("oldprice")
        if len(oldprices) > 1:
            violations.append(f"OFFER_FIELD_COUNT:{offer_id}:oldprice:{len(oldprices)}")
        if oldprices:
            try:
                oldprice = Decimal(str(oldprices[0].text or ""))
                if price is None or oldprice <= price:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                violations.append(f"INVALID_OLDPRICE:{offer_id}:{oldprices[0].text}")
        quantity_values = [node for node in offer.findall("param")
                           if node.get("name") == (policy.quantity_param_name if policy else "quantity")]
        if len(quantity_values) > 1:
            violations.append(f"DUPLICATE_QUANTITY_PARAM:{offer_id}")
        if quantity_values:
            try:
                if Decimal(str(quantity_values[0].text or "")) < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                violations.append(f"INVALID_QUANTITY:{offer_id}:{quantity_values[0].text}")
        vendors = offer.findall("vendor")
        if len(vendors) > 1:
            violations.append(f"OFFER_FIELD_COUNT:{offer_id}:vendor:{len(vendors)}")
        vendor_codes = offer.findall("vendorCode")
        if len(vendor_codes) > 1:
            violations.append(f"OFFER_FIELD_COUNT:{offer_id}:vendorCode:{len(vendor_codes)}")
        sterbrust_articles = [node for node in offer.findall("param") if node.get("name") == "Артикул Sterbrust"]
        if len(sterbrust_articles) > 1:
            violations.append(f"DUPLICATE_STERBRUST_ARTICLE_PARAM:{offer_id}")
        supplier_article_params = [node for node in offer.findall("param")
                                   if node.get("name") in {"Артикул поставщика", "Артикул Partner", "Артикул Optimum"}]
        if supplier_article_params:
            violations.append(f"DUPLICATE_SUPPLIER_ARTICLE_PARAM:{offer_id}")
        parsed_offers[offer_id] = {
            **values,
            "id": offer_id,
            "available": available,
            "vendor": str(vendors[0].text or "") if vendors else None,
            "vendorCode": str(vendor_codes[0].text or "").strip() or None if vendor_codes else None,
            "sterbrust_article": str(sterbrust_articles[0].text or "").strip() or None if sterbrust_articles else None,
            "oldprice": str(oldprices[0].text or "") if oldprices else None,
            "quantity": str(quantity_values[0].text or "") if quantity_values else None,
        }

    if expected_rows is not None:
        if policy is None:
            violations.append("EXPECTED_ROWS_REQUIRE_POLICY")
        else:
            expected = {_expected_offer_values(row, policy)["id"]: _expected_offer_values(row, policy)
                        for row in expected_rows}
            if set(parsed_offers) != set(expected):
                missing = sorted(set(expected) - set(parsed_offers), key=_id_sort_key)
                extra = sorted(set(parsed_offers) - set(expected), key=_id_sort_key)
                if missing:
                    violations.append("MISSING_EXPECTED_OFFERS:" + ",".join(missing))
                if extra:
                    violations.append("UNEXPECTED_OFFERS:" + ",".join(extra))
            for offer_id in sorted(set(expected) & set(parsed_offers), key=_id_sort_key):
                for key, expected_value in expected[offer_id].items():
                    if parsed_offers[offer_id].get(key) != expected_value:
                        violations.append(
                            f"DB_FIELD_MISMATCH:{offer_id}:{key}:"
                            f"{parsed_offers[offer_id].get(key)!r}!={expected_value!r}"
                        )
    return FeedValidationResult(not violations, tuple(violations), metrics)


def atomic_replace_validated(path: str | Path, data: bytes,
                             validator: Callable[[bytes], FeedValidationResult]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        written = temporary_path.read_bytes()
        result = validator(written)
        if not result.valid:
            raise FeedGenerationError(result.violations)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def csv_bytes(rows: Iterable[dict[str, Any]], fields: list[str]) -> bytes:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


class PostgresFeedStore:
    """Reads the complete selected state and records only internal feed-run metadata."""

    def __init__(self, connection) -> None:
        self.connection = connection

    @staticmethod
    def _dict_rows(cursor) -> list[dict[str, Any]]:
        names = [item.name for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def load_snapshot(self) -> FeedSnapshot:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT selection_status,count(*) AS row_count
                   FROM catalog_offer_selection GROUP BY selection_status ORDER BY selection_status"""
            )
            selection_counts = {str(status): int(count) for status, count in cursor.fetchall()}
            cursor.execute(
                """SELECT cs.catalog_product_id,cs.selection_status,cs.selected_offer_id,
                          cs.rule_version AS selection_rule_version,
                          cs.evidence->>'selection_reason' AS selection_reason,
                          cs.evaluated_at AS selection_evaluated_at,
                          o.id AS offer_id,o.supplier_id AS offer_supplier_id,
                          sp.id AS source_product_id,sp.supplier_id AS source_supplier_id,
                          sp.catalog_product_id AS source_catalog_product_id,
                          s.code AS supplier_code,s.enabled AS supplier_enabled,
                          sp.external_id AS supplier_external_id,
                          nullif(btrim(coalesce(o.sku, sp.sku)), '') AS supplier_sku,
                          o.price,o.old_price,o.currency,o.availability_normalized,o.availability_raw,
                          o.quantity,o.active AS offer_active,sp.active AS source_active,
                          sb.sterbrust_product_id,sb.name AS sterbrust_name,
                          sb.product_url AS sterbrust_url,sb.category_id AS sterbrust_category_id,
                          sb.category_name AS sterbrust_category_name,
                          coalesce(nullif(sb.brand_raw,''),nullif(sb.normalized_brand,'')) AS sterbrust_brand,
                          sb.active AS sterbrust_active,
                          nullif(btrim(sb.article_raw), '') AS sterbrust_article,
                          pm.status AS current_match_status,
                          pm.catalog_product_id AS current_match_catalog_product_id,
                          o.price_type,
                          CASE
                            WHEN pid.source_product_id IS NULL THEN NULL
                            WHEN pid.decision IN ('EXACT_EXISTING','HIGH_CONFIDENCE_EXISTING')
                              THEN nullif(btrim(pid.best_sterbrust_id), '')
                            ELSE ''
                          END AS confirmed_identity_id
                   FROM catalog_offer_selection cs
                   LEFT JOIN offers o ON o.id=cs.selected_offer_id
                   LEFT JOIN source_products sp ON sp.id=o.source_product_id
                   LEFT JOIN suppliers s ON s.id=o.supplier_id
                   LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=cs.catalog_product_id
                   LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
                   LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
                   WHERE cs.selection_status='selected'
                   ORDER BY cs.catalog_product_id,o.id,sb.sterbrust_product_id"""
            )
            selected = tuple(FeedRow(**row) for row in self._dict_rows(cursor))
            cursor.execute(
                """SELECT cs.catalog_product_id,coalesce(sb.sterbrust_product_id,'') AS sterbrust_product_id,
                          coalesce(sb.name,cp.name) AS sterbrust_name,cs.selection_status,
                          coalesce(cs.evidence->>'selection_reason','no_eligible_offer') AS reason
                   FROM catalog_offer_selection cs
                   JOIN catalog_products cp ON cp.id=cs.catalog_product_id
                   LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=cs.catalog_product_id
                   WHERE cs.selection_status='no_eligible_offer'
                   ORDER BY cs.catalog_product_id,sb.sterbrust_product_id"""
            )
            not_managed = tuple(self._dict_rows(cursor))
            snapshot_timestamp = self._resolve_snapshot_timestamp(cursor)
        return FeedSnapshot(selected, not_managed, selection_counts, snapshot_timestamp)

    @staticmethod
    def _resolve_snapshot_timestamp(cursor) -> datetime | None:
        """Resolve a deterministic feed timestamp using persisted state only.

        Priority is: selection evaluation time; a completed Stage 5 combined
        feed run; finally the newest persisted timestamp of the exact selected
        offer/source rows.  Wall-clock time is deliberately never used.
        """
        cursor.execute(
            """SELECT max(evaluated_at) FROM catalog_offer_selection
               WHERE selection_status='selected'"""
        )
        value = cursor.fetchone()[0]
        if value is not None:
            return value
        cursor.execute(
            """SELECT max(coalesce(finished_at,started_at)) FROM feed_runs
               WHERE feed_scope='combined' AND status='succeeded'
                 AND (metadata->>'stage'='5' OR metadata->>'feed_policy_version' IS NOT NULL)"""
        )
        value = cursor.fetchone()[0]
        if value is not None:
            return value
        cursor.execute(
            """SELECT max(ts) FROM (
                 SELECT greatest(o.updated_at,o.last_seen_at,sp.updated_at,sp.last_seen_at) AS ts
                 FROM catalog_offer_selection cs
                 JOIN offers o ON o.id=cs.selected_offer_id
                 JOIN source_products sp ON sp.id=o.source_product_id
                 WHERE cs.selection_status='selected'
               ) selected_state"""
        )
        return cursor.fetchone()[0]

    def start_feed_run(self, *, file_path: str, policy: FeedPolicy) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO feed_runs
                   (feed_scope,supplier_id,feed_type,file_path,status,metadata)
                   VALUES ('combined',NULL,%s,%s,'running',%s::jsonb) RETURNING id""",
                (policy.feed_type, file_path, json.dumps({
                    "feed_policy_version": policy.version,
                    "stage": 5,
                    "sterbrust_write_methods_used": 0,
                }, ensure_ascii=False, sort_keys=True)),
            )
            return int(cursor.fetchone()[0])

    def finish_feed_run(self, run_id: int, artifact: FeedArtifact) -> None:
        manifest = artifact.manifest
        metadata = {
            "stage": 5,
            "feed_policy_version": manifest["feed_policy_version"],
            "selection_policy_version": manifest["selection_policy_version"],
            "selected_count": manifest["db_selected_count"],
            "exported_count": manifest["exported_offer_count"],
            "availability_counts": manifest["availability_counts"],
            "available_true_count": manifest["available_true_count"],
            "available_false_count": manifest["available_false_count"],
            "sterbrust_write_methods_used": 0,
            "upload": "NO",
            "cron_configured": "NO",
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE feed_runs SET finished_at=now(),product_count=%s,offer_count=%s,
                          checksum=%s,status='succeeded',error=NULL,metadata=%s::jsonb
                   WHERE id=%s AND status='running'""",
                (manifest["exported_offer_count"], manifest["exported_offer_count"],
                 artifact.sha256, json.dumps(metadata, ensure_ascii=False, sort_keys=True), run_id),
            )
            if cursor.rowcount != 1:
                raise FeedGenerationError([f"FEED_RUN_SUCCESS_UPDATE_FAILED:{run_id}"])

    def fail_feed_run(self, run_id: int, error: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE feed_runs SET finished_at=now(),status='failed',error=%s
                   WHERE id=%s AND status='running'""",
                (error[:10000], run_id),
            )


TRACE_FIELDS = [
    "feed_offer_id", "sterbrust_product_id", "catalog_product_id", "sterbrust_name",
    "sterbrust_category_id", "selected_offer_id", "source_product_id", "supplier_code",
    "supplier_external_id", "supplier_sku", "price", "old_price", "currency",
    "availability_normalized", "availability_raw", "quantity", "selection_rule_version",
    "selection_reason", "feed_available", "feed_export_result",
]

NOT_MANAGED_FIELDS = [
    "catalog_product_id", "sterbrust_product_id", "sterbrust_name", "selection_status", "reason",
]


def write_core_reports(artifact: FeedArtifact, snapshot: FeedSnapshot,
                       reports_dir: str | Path) -> None:
    reports = Path(reports_dir)
    atomic_write_bytes(reports / "STAGE5_FEED_TRACE.csv", csv_bytes(artifact.trace_rows, TRACE_FIELDS))
    atomic_write_bytes(
        reports / "STAGE5_NOT_MANAGED_PRODUCTS.csv",
        csv_bytes(snapshot.not_managed_rows, NOT_MANAGED_FIELDS),
    )
    atomic_write_bytes(
        reports / "STAGE5_FEED_VALIDATION.json",
        (json.dumps(artifact.validation.as_jsonable(), ensure_ascii=False, indent=2, sort_keys=True)
         + "\n").encode("utf-8"),
    )
    atomic_write_bytes(
        reports / "STAGE5_FEED_MANIFEST.json",
        (json.dumps(artifact.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
