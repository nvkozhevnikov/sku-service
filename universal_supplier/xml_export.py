"""Read-only XML export: Stage 5 canonical preview and supplier diagnostic dump."""
from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from universal_supplier.stage6e import safe_export_filename
from universal_supplier.yml_feed import (
    FeedArtifact, FeedGenerationError, FeedPolicy, FeedSnapshot, build_yml,
    selected_row_violations,
)


FALSE_MATCH_GUARDS = (
    ("partner_st", "1655", "61421"),
    ("partner_st", "1656", "40495"),
    ("partner_st", "1835", "96887"),
    ("partner_st", "1837", "96887"),
    ("optimum", "561", "18451"),
    ("optimum", "782", "19862"),
)
SECRET_KEYS = frozenset({
    "password", "password_hash", "session", "session_secret", "token", "cookie",
    "proxy_credentials", "csrf_secret", "signing_secret", "db_password",
})
POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "yml_feed.json"
CANONICAL_NOTICE = (
    "Предпросмотр. Формат действующего профиля импорта ESOL ещё не подтверждён. "
    "Этот файл автоматически не отправляется на Sterbrust."
)
CONTRACT_NOTICE = "ESOL import contract: НЕ ПОДТВЕРЖДЁН"

SHORT_XML_REASON_PREFIXES = {
    "without_price": ("NULL_SELECTED_PRICE", "NON_POSITIVE_SELECTED_PRICE"),
    "without_url": ("INVALID_STERBRUST_URL",),
    "without_category": ("MISSING_STERBRUST_CATEGORY_ID", "MISSING_STERBRUST_CATEGORY_NAME"),
    "blocked_by_identity": (
        "NO_CURRENT_ACCEPTED_MATCH", "MATCH_CATALOG_PRODUCT_MISMATCH",
        "MISSING_STERBRUST_PRODUCT_ID",
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _xml_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _append(parent: ET.Element, tag: str, value: Any) -> ET.Element:
    node = ET.SubElement(parent, tag)
    text = _xml_text(value)
    if text:
        node.text = text
    return node


def canonical_false_match_violations(artifact: FeedArtifact) -> list[str]:
    violations: list[str] = []
    for supplier, external_id, prohibited in FALSE_MATCH_GUARDS:
        leaks = [
            row for row in artifact.trace_rows
            if row.get("supplier_code") == supplier and str(row.get("supplier_external_id")) == external_id
        ]
        if leaks:
            violations.append(f"KNOWN_FALSE_MATCH_LEAK:{supplier}:{external_id}")
        if any(str(row.get("feed_offer_id")) == prohibited for row in leaks):
            violations.append(f"KNOWN_FALSE_MATCH_TARGET:{supplier}:{external_id}:{prohibited}")
    mrx = [row for row in artifact.trace_rows if str(row.get("supplier_external_id")) == "305"]
    if mrx:
        violations.append("MRX3_FEED_LEAK")
    return violations


def pp800f_in_artifact(artifact: FeedArtifact) -> bool:
    return any(
        str(row.get("supplier_external_id")) == "297" and str(row.get("feed_offer_id")) == "88585"
        for row in artifact.trace_rows
    )


def prepare_canonical_snapshot(
    snapshot: FeedSnapshot, policy: FeedPolicy,
) -> tuple[FeedSnapshot, dict[str, int]]:
    """Exclude invalid selected rows without weakening feed validation.

    The persisted selection remains untouched. Every included row still passes the
    complete Stage 5 row contract, while invalid rows are counted by operator-facing
    reason and omitted from this preview artifact.
    """
    included = []
    counts: Counter[str] = Counter()
    for row in snapshot.selected_rows:
        violations = selected_row_violations((row,), policy, 1)
        if not violations:
            included.append(row)
            continue
        counts["excluded_total"] += 1
        matched = False
        prefixes = {violation.split(":", 1)[0] for violation in violations}
        for reason, reason_prefixes in SHORT_XML_REASON_PREFIXES.items():
            if prefixes.intersection(reason_prefixes):
                counts[reason] += 1
                matched = True
        if not matched or prefixes.difference(
            prefix for values in SHORT_XML_REASON_PREFIXES.values() for prefix in values
        ):
            counts["invalid_selected_data"] += 1
    prepared = FeedSnapshot(
        selected_rows=tuple(included),
        not_managed_rows=snapshot.not_managed_rows,
        selection_counts={**snapshot.selection_counts, "selected": len(included)},
        snapshot_timestamp=snapshot.snapshot_timestamp,
    )
    result = {
        "excluded_total": 0,
        "without_price": 0,
        "without_url": 0,
        "without_category": 0,
        "blocked_by_identity": 0,
        "invalid_selected_data": 0,
    }
    result.update(counts)
    return prepared, result


def generate_canonical_xml(snapshot: FeedSnapshot, policy: FeedPolicy | None = None) -> FeedArtifact:
    policy = policy or FeedPolicy.load(POLICY_PATH)
    prepared, exclusion_counts = prepare_canonical_snapshot(snapshot, policy)
    artifact = build_yml(prepared, policy)
    artifact.manifest["selected_rows_total"] = len(snapshot.selected_rows)
    artifact.manifest["short_xml_exclusion_counts"] = exclusion_counts
    extra = canonical_false_match_violations(artifact)
    if extra:
        raise FeedGenerationError(extra)
    return artifact


def preview_xml_text(data: bytes, *, max_lines: int = 80, max_offers: int = 10) -> str:
    text = data.decode("utf-8")
    lines = text.splitlines()
    offer_lines = 0
    kept: list[str] = []
    for line in lines:
        kept.append(line)
        if "<offer " in line or "<product " in line:
            offer_lines += 1
        if len(kept) >= max_lines or offer_lines >= max_offers:
            break
    return html.escape("\n".join(kept), quote=False)


def xml_stats(data: bytes) -> dict[str, Any]:
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "generated_at": _now().isoformat(),
        "lines": data.count(b"\n") + 1,
    }


def _product_element(row: Mapping[str, Any], details: Mapping[str, list[dict[str, Any]]]) -> ET.Element:
    product = ET.Element("product", {"source_product_id": _xml_text(row.get("source_product_id")),
                                     "external_id": _xml_text(row.get("external_id"))})
    identity = ET.SubElement(product, "identity")
    _append(identity, "supplier_code", row.get("supplier_code"))
    _append(identity, "supplier_external_id", row.get("external_id"))
    _append(identity, "external_id_is_stable", row.get("external_id_is_stable"))
    _append(identity, "supplier_sku", row.get("sku") or row.get("article"))
    _append(identity, "product_kind", row.get("product_kind"))
    _append(identity, "own_model", row.get("own_model"))
    _append(identity, "reference_model", row.get("reference_model"))
    if row.get("confirmed_sterbrust_id"):
        _append(identity, "sterbrust_product_id", row.get("confirmed_sterbrust_id"))
        _append(identity, "stb_1c_bitrix_id", row.get("confirmed_sterbrust_id"))
    matching = ET.SubElement(product, "matching")
    _append(matching, "decision", row.get("decision"))
    if row.get("proposed_sterbrust_id") and not row.get("confirmed_sterbrust_id"):
        _append(matching, "proposed_sterbrust_id", row.get("proposed_sterbrust_id"))
    _append(matching, "reason", row.get("decision_reason"))
    _append(matching, "rule_version", row.get("rule_version"))
    _append(matching, "audited_at", row.get("audited_at"))
    _append(matching, "conflicts", row.get("conflicts"))
    _append(matching, "identity_characteristics", row.get("identity_characteristics"))
    content = ET.SubElement(product, "content")
    _append(content, "name", row.get("name"))
    _append(content, "brand_raw", row.get("brand_raw"))
    _append(content, "manufacturer_raw", row.get("manufacturer_raw"))
    _append(content, "category_path", row.get("category"))
    _append(content, "description_text", row.get("description_text"))
    _append(content, "description_html", row.get("description_html"))
    commercial = ET.SubElement(product, "commercial")
    _append(commercial, "price", row.get("price"))
    _append(commercial, "old_price", row.get("old_price"))
    _append(commercial, "currency", row.get("currency"))
    _append(commercial, "price_type", row.get("price_type"))
    _append(commercial, "price_raw", row.get("price_raw"))
    _append(commercial, "price_source", row.get("price_source"))
    _append(commercial, "availability_raw", row.get("availability_raw"))
    _append(commercial, "availability_normalized", row.get("availability_normalized"))
    _append(commercial, "quantity", row.get("quantity"))
    properties = ET.SubElement(product, "properties")
    for item in details.get("properties", ()):
        node = ET.SubElement(properties, "property", {
            "name": _xml_text(item.get("raw_name")),
            "raw": _xml_text(item.get("raw_value")),
            "unit": _xml_text(item.get("raw_unit")), "normalized_name": _xml_text(item.get("normalized_name")),
            "normalized_value": _xml_text(item.get("normalized_value")), "normalized_unit": _xml_text(item.get("normalized_unit")),
            "mapping_status": _xml_text(item.get("mapping_status")), "canonical_code": _xml_text(item.get("canonical_code")),
            "canonical_name": _xml_text(item.get("canonical_name")), "canonical_unit": _xml_text(item.get("canonical_unit")),
        })
    media = ET.SubElement(product, "media")
    for item in details.get("images", ()):
        ET.SubElement(media, "image", {
            "url": _xml_text(item.get("source_url")),
            "primary": _xml_text(item.get("is_primary")),
        })
    for item in details.get("documents", ()):
        ET.SubElement(media, "document", {
            "url": _xml_text(item.get("source_url")),
            "type": _xml_text(item.get("document_type")),
        })
    relations = ET.SubElement(product, "relations")
    for item in details.get("relations", ()):
        ET.SubElement(relations, "relation", {
            "type": _xml_text(item.get("relation_type")),
            "to_source_product_id": _xml_text(item.get("to_source_product_id")),
        })
    options = ET.SubElement(product, "options")
    for item in details.get("options", ()):
        ET.SubElement(options, "option_value", {key: _xml_text(item.get(key)) for key in (
            "option_name", "option_position", "raw_value", "position", "target_url",
            "variant_internal_id", "classification", "is_selected")})
    provenance = ET.SubElement(product, "provenance")
    _append(provenance, "source_url", row.get("source_url"))
    _append(provenance, "canonical_url", row.get("canonical_url"))
    for key in ("first_seen_at", "last_seen_at", "last_success_at", "last_changed_at", "last_http_status", "active"):
        _append(provenance, key, row.get(key))
    for item in details.get("provenance", ()):
        ET.SubElement(provenance, "field", {key: _xml_text(item.get(key)) for key in (
            "field_name", "source_kind", "source_path", "observed_at", "normalized_by")})
    return product


def iter_full_xml(rows: Iterable[Mapping[str, Any]],
                      details_for: Any = None) -> Iterator[bytes]:
    yield (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<supplier_export schema_version="stage6e-full-v1" read_only="true">\n'
        f'<notice>{escape("Полная безопасная выгрузка. Не является файлом импорта Sterbrust.")}</notice>\n'
        f'<contract>{escape(CONTRACT_NOTICE)}</contract>\n'
    ).encode("utf-8")
    current_supplier = None
    batch: list[Mapping[str, Any]] = []

    def flush(items: list[Mapping[str, Any]]) -> Iterator[bytes]:
        ids = [int(item["source_product_id"]) for item in items if str(item.get("source_product_id", "")).isdigit()]
        grouped: dict[str, dict[int, list]] = {key: {} for key in ("properties", "images", "documents", "relations", "options", "provenance")}
        if details_for and ids:
            raw = details_for(ids)
            for kind in grouped:
                for item in raw.get(kind, ()):
                    grouped[kind].setdefault(int(item["source_product_id"]), []).append(item)
        for item in items:
            pid = int(item["source_product_id"]) if str(item.get("source_product_id", "")).isdigit() else 0
            details = {kind: grouped[kind].get(pid, []) for kind in grouped}
            node = _product_element(item, details)
            yield ET.tostring(node, encoding="utf-8") + b"\n"

    for row in rows:
        code = str(row.get("supplier_code") or "unknown")
        if current_supplier is None:
            current_supplier = code
            yield f'<supplier code="{escape(code)}"><products>\n'.encode("utf-8")
        elif code != current_supplier:
            if batch:
                yield from flush(batch); batch = []
            yield b"</products></supplier>\n"
            current_supplier = code
            yield f'<supplier code="{escape(code)}"><products>\n'.encode("utf-8")
        batch.append(row)
        if len(batch) >= 200:
            yield from flush(batch); batch = []
    if batch:
        yield from flush(batch)
    if current_supplier is not None:
        yield b"</products></supplier>\n"
    yield b"</supplier_export>\n"


iter_supplier_xml = iter_full_xml


def supplier_xml_bytes(rows: Iterable[Mapping[str, Any]], details_for=None) -> bytes:
    return b"".join(iter_full_xml(rows, details_for))


def canonical_filename(supplier: str = "") -> str:
    return safe_export_filename("sterbrust_preview", "xml", supplier)


def supplier_filename(supplier: str = "") -> str:
    kind = f"supplier_{supplier}" if supplier else "supplier_all"
    return safe_export_filename(kind, "xml", "")


class XmlExportService:
    def __init__(self, store, *, policy: FeedPolicy | None = None):
        self.store = store
        self.policy = policy or FeedPolicy.load(POLICY_PATH)

    def canonical_summary(self, supplier: str = "") -> dict[str, int]:
        if hasattr(self.store, "xml_canonical_summary"):
            return self.store.xml_canonical_summary(supplier)
        return {
            "considered": 0, "selected": 0, "no_selected_offer": 0, "blocked_by_conflict": 0,
            "needs_review": 0, "safe_new": 0, "accessories": 0, "excluded_total": 0,
            "without_price": 0, "without_url": 0, "without_category": 0,
            "blocked_by_identity": 0, "invalid_selected_data": 0,
        }

    def canonical_artifact(self, supplier: str = "") -> FeedArtifact:
        if hasattr(self.store, "xml_canonical_snapshot"):
            snapshot = self.store.xml_canonical_snapshot(supplier)
        else:
            snapshot = FeedSnapshot((), (), {"selected": 0}, _now())
        return generate_canonical_xml(snapshot, self.policy)

    def supplier_rows(self, filters) -> list[dict[str, Any]]:
        if hasattr(self.store, "iter_export_rows"):
            return list(self.store.iter_export_rows(filters))
        return list(self.store.export_rows(filters))

    def iter_full(self, filters):
        rows = self.store.iter_export_rows(filters) if hasattr(self.store, "iter_export_rows") else self.store.export_rows(filters)
        return iter_full_xml(rows, self.supplier_details)

    def supplier_details(self, ids: list[int]) -> dict[str, list[dict[str, Any]]]:
        if hasattr(self.store, "export_details"):
            return self.store.export_details(ids)
        return {key: [] for key in ("properties", "images", "documents", "relations")}
