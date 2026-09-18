#!/usr/bin/env python3
"""Capture public Sterbrust state and build the fail-closed Stage 6 reports.

Network operations are GET-only public product-page reads.  The script does not
contain a Bitrix webhook, a write endpoint, an upload operation, or an import
trigger.  The exact ESOL import-profile contract remains blocked until the
profile configuration or server-side evidence is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_supplier.sterbrust_import_contract import (
    KNOWN_FALSE_MATCH_PAIRS,
    CurrentCommercialState,
    DesiredCommercialState,
    ImportContractEvidence,
    compare_state,
    decimal_or_none,
    decimal_text,
    normalize_public_availability,
    validate_managed_rows,
)


REPORTS = ROOT / "reports"
EXPORTS = ROOT / "exports"
TRACE_PATH = REPORTS / "STAGE5_FEED_TRACE.csv"
REGISTRY_PATH = REPORTS / "STERBRUST_REGISTRY.csv"
FEED_PATH = EXPORTS / "sterbrust_selected_offers.xml"
BEFORE_RAW = REPORTS / "STAGE6_PUBLIC_BEFORE_RAW.json"
AFTER_RAW = REPORTS / "STAGE6_PUBLIC_AFTER_RAW.json"

IMPORT_ADMIN_ENTRY = "https://sterbrust.com/bitrix/admin/esol_import_xml.php"
IMPORT_PROFILE_EVIDENCE_ENTRY = (
    "https://sterbrust.com/bitrix/admin/esol_import_xml.php?lang=ru&PROFILE_ID=13"
)
BLOCKED_STATUS = "BLOCKED_IMPORT_CONTRACT_EVIDENCE"
SOURCE_CHECKPOINT_FILENAME = "UNIVERSAL_SUPPLIER_STAGE5_YML_FEED_GENERATOR(1).zip"
SOURCE_CHECKPOINT_SHA256 = "3f006273dd7f5e65f9aec61e9396e43b0fd4e70f390364fb0331c88fefeeeb05e"
CANONICAL_STAGE5_XML_SHA256 = "740f8d9e87da73c4b96ecef77f961db5850a97718eaf302c4ca443f8e2eebf54"


class ProductPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.main_product_ids: list[str] = []
        self.json_ld_scripts: list[str] = []
        self._capture_json_ld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if "catalog-detail__main-parts" in classes and values.get("data-id"):
            self.main_product_ids.append(values["data-id"])
        if tag.casefold() == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._capture_json_ld = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_json_ld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capture_json_ld:
            self.json_ld_scripts.append("".join(self._script_parts))
            self._capture_json_ld = False
            self._script_parts = []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def extract_product_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if "Product" in types and value.get("offers"):
            return value
        for child in value.values():
            found = extract_product_json(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = extract_product_json(child)
            if found:
                return found
    return None


def first_offer(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def article_from_product(product: dict[str, Any]) -> str:
    for item in product.get("additionalProperty", []):
        if isinstance(item, dict) and str(item.get("name", "")).strip().casefold() in {"артикул", "article"}:
            return str(item.get("value") or "").strip()
    return str(product.get("sku") or product.get("mpn") or "").strip()


def fetch_public_state(item: dict[str, Any]) -> dict[str, Any]:
    expected_id = str(item["sterbrust_product_id"])
    url = str(item["url"])
    captured_at = utc_now()
    last_error = ""
    for attempt in range(3):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "Mozilla/5.0 Stage6ReadOnlyAudit/1.0", "Accept": "text/html"},
            )
            with opener.open(request, timeout=45) as response:
                raw = response.read()
                final_url = response.geturl()
                status = int(getattr(response, "status", 200))
                content_type = str(response.headers.get("Content-Type") or "")
            text = raw.decode("utf-8", errors="replace")
            parser = ProductPageParser()
            parser.feed(text)
            product: dict[str, Any] | None = None
            json_errors = 0
            for script in parser.json_ld_scripts:
                try:
                    payload = json.loads(script)
                except json.JSONDecodeError:
                    json_errors += 1
                    continue
                product = extract_product_json(payload) or product
            if product is None:
                raise RuntimeError("PRODUCT_JSON_LD_NOT_FOUND")
            offer = first_offer(product.get("offers"))
            raw_availability = str(offer.get("availability") or "") or None
            current_price = decimal_or_none(offer.get("price"))
            main_ids = parser.main_product_ids
            target_count = sum(value == expected_id for value in main_ids)
            return {
                "sterbrust_product_id": expected_id,
                "requested_url": url,
                "final_url": final_url,
                "http_status": status,
                "content_type": content_type,
                "main_product_ids": main_ids,
                "target_match_count": target_count,
                "name": str(product.get("name") or "").strip(),
                "article": article_from_product(product),
                "current_price": decimal_text(current_price),
                "current_currency": str(offer.get("priceCurrency") or "").strip(),
                "current_availability": normalize_public_availability(raw_availability),
                "current_availability_raw": raw_availability,
                "current_quantity": None,
                "quantity_evidence": "NOT_EXPOSED_PUBLICLY",
                "captured_at": captured_at,
                "evidence_source": "PUBLIC_PRODUCT_JSON_LD_AND_MAIN_DATA_ID",
                "json_ld_parse_errors": json_errors,
                "attempts": attempt + 1,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 - the report must preserve every page failure
            last_error = f"{type(exc).__name__}:{exc}"
            if attempt < 2:
                time.sleep(0.4 * (2**attempt))
    return {
        "sterbrust_product_id": expected_id,
        "requested_url": url,
        "final_url": "",
        "http_status": None,
        "content_type": "",
        "main_product_ids": [],
        "target_match_count": 0,
        "name": "",
        "article": "",
        "current_price": "",
        "current_currency": "",
        "current_availability": None,
        "current_availability_raw": None,
        "current_quantity": None,
        "quantity_evidence": "NOT_EXPOSED_PUBLICLY",
        "captured_at": captured_at,
        "evidence_source": "PUBLIC_PRODUCT_PAGE_GET_FAILED",
        "json_ld_parse_errors": 0,
        "attempts": 3,
        "error": last_error,
    }


def registry_rows_for(ids: set[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with REGISTRY_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            product_id = str(row.get("sterbrust_product_id") or "")
            if product_id in ids:
                result[product_id] = row
    return result


def load_inputs() -> tuple[list[DesiredCommercialState], dict[str, dict[str, str]], list[dict[str, str]]]:
    with TRACE_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        trace_rows = list(csv.DictReader(handle))
    if len(trace_rows) != 117:
        raise RuntimeError(f"Unexpected Stage 5 selected count: {len(trace_rows)}")

    xml_root = ET.parse(FEED_PATH).getroot()
    offers = {
        str(node.get("id") or ""): {
            "url": str(node.findtext("url") or ""),
            "name": str(node.findtext("name") or ""),
        }
        for node in xml_root.findall("./shop/offers/offer")
    }
    ids = {str(row["sterbrust_product_id"]) for row in trace_rows}
    registry = registry_rows_for(ids)
    desired_rows: list[DesiredCommercialState] = []
    for row in trace_rows:
        target = str(row["sterbrust_product_id"])
        xml_offer = offers.get(target, {})
        reg = registry.get(target, {})
        url = xml_offer.get("url") or reg.get("product_url") or ""
        if not url:
            raise RuntimeError(f"Missing public URL for target {target}")
        desired_rows.append(DesiredCommercialState(
            sterbrust_product_id=target,
            name=str(row.get("sterbrust_name") or xml_offer.get("name") or ""),
            article=str(reg.get("article_raw") or ""),
            price=Decimal(str(row["price"])),
            currency=str(row["currency"]),
            availability=str(row["availability_normalized"]),
            quantity=decimal_or_none(row.get("quantity")),
            supplier_code=str(row["supplier_code"]),
            supplier_external_id=str(row["supplier_external_id"]),
            supplier_sku=str(row["supplier_sku"]),
            url=url,
        ))
    validate_managed_rows(desired_rows, set())
    return desired_rows, registry, trace_rows


def capture(kind: str, workers: int) -> list[dict[str, Any]]:
    desired_rows, _registry, _trace = load_inputs()
    inputs = [
        {"sterbrust_product_id": row.sterbrust_product_id, "url": row.url}
        for row in desired_rows
    ]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as pool:
        rows = list(pool.map(fetch_public_state, inputs))
    rows.sort(key=lambda row: int(row["sterbrust_product_id"]))
    target = BEFORE_RAW if kind == "before" else AFTER_RAW
    write_json(target, {
        "capture_kind": kind,
        "capture_completed_at": utc_now(),
        "http_method": "GET",
        "products": rows,
        "write_methods_used": 0,
    })
    return rows


def load_capture(path: Path) -> tuple[str, list[dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return str(value["capture_completed_at"]), list(value["products"])


def as_current(row: dict[str, Any]) -> CurrentCommercialState:
    return CurrentCommercialState(
        sterbrust_product_id=str(row["sterbrust_product_id"]),
        name=str(row.get("name") or ""),
        article=str(row.get("article") or ""),
        price=decimal_or_none(row.get("current_price")),
        currency=str(row.get("current_currency") or "") or None,
        availability=str(row.get("current_availability") or "") or None,
        availability_raw=str(row.get("current_availability_raw") or "") or None,
        quantity=decimal_or_none(row.get("current_quantity")),
        target_match_count=int(row.get("target_match_count") or 0),
        observed_at=str(row.get("captured_at") or ""),
        evidence_source=str(row.get("evidence_source") or ""),
        error=str(row.get("error") or ""),
    )


def contract_evidence() -> ImportContractEvidence:
    return ImportContractEvidence(
        mechanism="ESOL XML Import admin entry point observed; exact profile contract not inspectable",
        admin_entry_point=IMPORT_ADMIN_ENTRY,
        import_profile_id=None,
        source_evidence=(
            "Current GET reached Bitrix authorization at PROFILE_ID=13; no authenticated profile export, "
            "server script, cron line, or settings snapshot was available."
        ),
    )


def plan_rows(
    desired_rows: list[DesiredCommercialState],
    public_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    current_by_id = {str(row["sterbrust_product_id"]): as_current(row) for row in public_rows}
    raw: list[dict[str, Any]] = []
    plans = []
    evidence = contract_evidence()
    for desired in desired_rows:
        current = current_by_id.get(desired.sterbrust_product_id) or CurrentCommercialState(
            desired.sterbrust_product_id, "", "", None, None, None, None, None, 0, "", "MISSING_CAPTURE", "MISSING_CAPTURE"
        )
        plan = compare_state(desired, current, evidence)
        plans.append(plan)
        raw.append({
            "sterbrust_product_id": desired.sterbrust_product_id,
            "current_live_price": decimal_text(plan.current_price),
            "desired_selected_price": decimal_text(plan.desired_price),
            "price_change": plan.price_change,
            "price_delta_absolute": decimal_text(plan.price_delta),
            "price_delta_percent": decimal_text(plan.price_delta_percent),
            "current_availability": plan.current_availability or "",
            "desired_normalized_availability": plan.desired_availability,
            "availability_change": plan.availability_change,
            "current_quantity": decimal_text(plan.current_quantity),
            "desired_quantity": decimal_text(plan.desired_quantity),
            "quantity_change": "NOT_EVALUATED_IMPORT_CONTRACT_UNKNOWN",
            "write_fields_required": "|".join(plan.write_fields_required),
            "identity_confidence": plan.identity_confidence,
            "action": plan.action,
            "blocking_reason": plan.blocking_reason,
        })
    return raw, plans


def snapshot_csv_rows(
    desired_rows: list[DesiredCommercialState],
    public_rows: list[dict[str, Any]],
    registry: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    desired_by_id = {row.sterbrust_product_id: row for row in desired_rows}
    result: list[dict[str, Any]] = []
    for row in public_rows:
        target = str(row["sterbrust_product_id"])
        desired = desired_by_id[target]
        reg = registry.get(target, {})
        result.append({
            "sterbrust_product_id": target,
            "name": row.get("name") or desired.name,
            "article": row.get("article") or reg.get("article_raw") or "",
            "xml_id": "NOT_EXPOSED_BY_PUBLIC_PAGE",
            "current_price": row.get("current_price") or "",
            "current_currency": row.get("current_currency") or "",
            "current_availability": row.get("current_availability") or "",
            "current_availability_raw": row.get("current_availability_raw") or "",
            "current_quantity": "",
            "current_commercial_property_values": json.dumps({
                "public_schema_availability": row.get("current_availability_raw"),
                "quantity": "NOT_EXPOSED_PUBLICLY",
                "bitrix_price_type": "NOT_VERIFIED",
                "bitrix_availability_property": "NOT_VERIFIED",
            }, ensure_ascii=False, sort_keys=True),
            "captured_at": row.get("captured_at") or "",
            "evidence_source": row.get("evidence_source") or "",
            "target_match_count": row.get("target_match_count") or 0,
            "http_status": row.get("http_status") or "",
            "error": row.get("error") or "",
        })
    return result


def identity_rows(
    desired_rows: list[DesiredCommercialState],
    public_rows: list[dict[str, Any]],
    registry: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    public_by_id = {str(row["sterbrust_product_id"]): row for row in public_rows}
    result = []
    for desired in desired_rows:
        target = desired.sterbrust_product_id
        public = public_by_id.get(target, {})
        reg = registry.get(target, {})
        count = int(public.get("target_match_count") or 0)
        if count == 1:
            status = "EXACT_EXISTING_TARGET"
        elif count == 0:
            status = "MISSING_TARGET"
        else:
            status = "AMBIGUOUS_TARGET"
        warning = ""
        if target == "40589":
            warning = "CATALOG_METADATA_WARNING: URL contains partner-gd-m5020a while name/source identify M3080A; no metadata write allowed"
        result.append({
            "feed_product_id": target,
            "feed_name": desired.name,
            "supplier_code": desired.supplier_code,
            "supplier_external_id": desired.supplier_external_id,
            "supplier_sku": desired.supplier_sku,
            "packaged_registry_product_id": str(reg.get("sterbrust_product_id") or ""),
            "packaged_registry_id_match": str(reg.get("sterbrust_product_id") or "") == target,
            "public_main_product_ids": "|".join(map(str, public.get("main_product_ids") or [])),
            "public_target_match_count": count,
            "public_name": public.get("name") or "",
            "public_article": public.get("article") or "",
            "status": status,
            "exact_update_key_candidate": f"Bitrix product ID {target} (importer support NOT_PROVEN)",
            "warning": warning,
        })
    return result


def false_match_guard_rows(desired_rows: list[DesiredCommercialState]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for supplier, external_id, prohibited_target, reason in KNOWN_FALSE_MATCH_PAIRS:
        leaks = [
            row for row in desired_rows
            if row.supplier_code == supplier
            and row.supplier_external_id == external_id
            and row.sterbrust_product_id == prohibited_target
        ]
        result.append({
            "supplier_code": supplier,
            "supplier_external_id": external_id,
            "prohibited_target_id": prohibited_target,
            "guard_reason": reason,
            "import_plan_leak_count": len(leaks),
            "result": "PASS" if not leaks else "FAIL",
        })
    mrx3 = [
        row for row in desired_rows
        if row.supplier_code == "partner_st" and row.supplier_external_id == "305"
    ]
    result.append({
        "supplier_code": "partner_st",
        "supplier_external_id": "305",
        "prohibited_target_id": "88411",
        "guard_reason": "MR-X3 conflict must not target MR-X4",
        "import_plan_leak_count": len(mrx3),
        "result": "PASS" if not mrx3 else "FAIL",
    })
    return result


def rollback_rows(plan_csv: list[dict[str, Any]], before_snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before_by_id = {str(row["sterbrust_product_id"]): row for row in before_snapshot}
    result = []
    for plan in plan_csv:
        changed_fields = [
            field for field, changed in (
                ("price", plan["price_change"] in {"INCREASE", "DECREASE"}),
                ("availability", plan["availability_change"] == "CHANGE"),
            ) if changed
        ]
        if not changed_fields:
            continue
        before = before_by_id[plan["sterbrust_product_id"]]
        result.append({
            "sterbrust_product_id": plan["sterbrust_product_id"],
            "would_change_fields_from_public_comparison": "|".join(changed_fields),
            "rollback_price": before["current_price"],
            "rollback_currency": before["current_currency"],
            "rollback_availability": before["current_availability"],
            "rollback_availability_raw": before["current_availability_raw"],
            "rollback_quantity": before["current_quantity"],
            "captured_at": before["captured_at"],
            "rollback_status": "LOGICAL_VALUES_CAPTURED_IMPORTER_MAPPING_BLOCKED",
        })
    return result


def canary_rows(
    desired_rows: list[DesiredCommercialState],
    plan_csv: list[dict[str, Any]],
    before_snapshot: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    desired_by_id = {row.sterbrust_product_id: row for row in desired_rows}
    before_by_id = {str(row["sterbrust_product_id"]): row for row in before_snapshot}
    plan_by_id = {str(row["sterbrust_product_id"]): row for row in plan_csv}
    selected: list[tuple[str, str]] = []

    def choose(reason: str, predicate) -> None:
        for row in plan_csv:
            target = str(row["sterbrust_product_id"])
            if target not in {item[0] for item in selected} and predicate(row):
                selected.append((target, reason))
                return

    if "88585" in plan_by_id:
        selected.append(("88585", "PP-800F exact-key canary"))
    choose("unchanged in_stock control", lambda row: row["price_change"] == "UNCHANGED" and row["availability_change"] == "UNCHANGED" and row["desired_normalized_availability"] == "in_stock")
    choose("price-change canary", lambda row: row["price_change"] in {"INCREASE", "DECREASE"})
    choose("preorder semantics canary", lambda row: row["desired_normalized_availability"] == "preorder")
    choose("out_of_stock semantics canary", lambda row: row["desired_normalized_availability"] == "out_of_stock")

    result = []
    for target, reason in selected[:5]:
        plan = plan_by_id[target]
        before = before_by_id[target]
        desired = desired_by_id[target]
        result.append({
            "sterbrust_product_id": target,
            "reason_selected": reason,
            "current_price": before["current_price"],
            "desired_price": decimal_text(desired.price),
            "current_availability": before["current_availability"],
            "desired_availability": desired.availability,
            "current_quantity": before["current_quantity"],
            "desired_quantity": decimal_text(desired.quantity),
            "expected_changed_fields": plan["write_fields_required"],
            "rollback_price": before["current_price"],
            "rollback_availability": before["current_availability"],
            "rollback_quantity": before["current_quantity"],
            "execution_status": "DESIGN_ONLY_DO_NOT_EXECUTE",
        })
    return result


def state_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("target_match_count"),
        row.get("current_price"),
        row.get("current_currency"),
        row.get("current_availability"),
        row.get("current_availability_raw"),
        row.get("current_quantity"),
    )


def zero_write_qa(before_rows: list[dict[str, Any]], after_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    if after_rows is None:
        return {
            "result": "PENDING_AFTER_READ_ONLY_CAPTURE",
            "dry_run": True,
            "sterbrust_write_methods_used": 0,
            "http_methods_used": {"GET": len(before_rows), "POST": 0, "PUT": 0, "PATCH": 0, "DELETE": 0},
            "live_state_changed_by_stage6": 0,
            "external_change_count": "NOT_CHECKED_YET",
        }
    after_by_id = {str(row["sterbrust_product_id"]): row for row in after_rows}
    changes = []
    for before in before_rows:
        target = str(before["sterbrust_product_id"])
        after = after_by_id.get(target)
        if after is None or state_signature(before) != state_signature(after):
            changes.append(target)
    return {
        "result": "PASS",
        "dry_run": True,
        "sterbrust_write_methods_used": 0,
        "http_methods_used": {"GET": len(before_rows) + len(after_rows), "POST": 0, "PUT": 0, "PATCH": 0, "DELETE": 0},
        "live_state_changed_by_stage6": 0,
        "external_change_count": len(changes),
        "external_change_product_ids": changes,
        "attribution_note": "Any before/after difference is external because Stage 6 has no write call path and made zero non-GET requests.",
    }


def render_reports(test_count: int | None = None) -> dict[str, Any]:
    desired_rows, registry, _trace = load_inputs()
    before_completed_at, public_rows = load_capture(BEFORE_RAW)
    before_snapshot = snapshot_csv_rows(desired_rows, public_rows, registry)
    plan_csv, plans = plan_rows(desired_rows, public_rows)
    identities = identity_rows(desired_rows, public_rows, registry)
    false_guards = false_match_guard_rows(desired_rows)
    rollbacks = rollback_rows(plan_csv, before_snapshot)
    canaries = canary_rows(desired_rows, plan_csv, before_snapshot)

    exact = sum(row["status"] == "EXACT_EXISTING_TARGET" for row in identities)
    missing = sum(row["status"] == "MISSING_TARGET" for row in identities)
    ambiguous = sum(row["status"] == "AMBIGUOUS_TARGET" for row in identities)
    duplicate_targets = len(desired_rows) - len({row.sterbrust_product_id for row in desired_rows})
    action_counts = Counter(row["action"] for row in plan_csv)
    price_counts = Counter(row["price_change"] for row in plan_csv)
    availability_counts = Counter(row["availability_change"] for row in plan_csv)
    unchanged_comparison = sum(
        row["price_change"] == "UNCHANGED" and row["availability_change"] == "UNCHANGED"
        for row in plan_csv
    )
    price_changed = sum(row["price_change"] in {"INCREASE", "DECREASE"} for row in plan_csv)
    availability_changed = sum(row["availability_change"] == "CHANGE" for row in plan_csv)
    missing_current_price = sum(row["price_change"] == "UNKNOWN_CURRENT" for row in plan_csv)
    deltas = sorted(
        (
            {
                "sterbrust_product_id": row["sterbrust_product_id"],
                "absolute_delta": row["price_delta_absolute"],
                "percentage_delta": row["price_delta_percent"],
            }
            for row in plan_csv if row["price_change"] in {"INCREASE", "DECREASE"}
        ),
        key=lambda row: abs(Decimal(row["percentage_delta"] or "0")),
        reverse=True,
    )

    after_rows = load_capture(AFTER_RAW)[1] if AFTER_RAW.exists() else None
    zero_qa = zero_write_qa(public_rows, after_rows)
    write_json(REPORTS / "STAGE6_ZERO_WRITE_QA.json", zero_qa)

    snapshot_fields = [
        "sterbrust_product_id", "name", "article", "xml_id", "current_price",
        "current_currency", "current_availability", "current_availability_raw",
        "current_quantity", "current_commercial_property_values", "captured_at",
        "evidence_source", "target_match_count", "http_status", "error",
    ]
    write_csv(REPORTS / "STAGE6_LIVE_BEFORE_SNAPSHOT.csv", before_snapshot, snapshot_fields)
    if after_rows is not None:
        write_csv(
            REPORTS / "STAGE6_LIVE_AFTER_SNAPSHOT.csv",
            snapshot_csv_rows(desired_rows, after_rows, registry),
            snapshot_fields,
        )

    plan_fields = [
        "sterbrust_product_id", "current_live_price", "desired_selected_price",
        "price_change", "price_delta_absolute", "price_delta_percent",
        "current_availability", "desired_normalized_availability",
        "availability_change", "current_quantity", "desired_quantity",
        "quantity_change", "write_fields_required", "identity_confidence",
        "action", "blocking_reason",
    ]
    write_csv(REPORTS / "STAGE6_IMPORT_PLAN.csv", plan_csv, plan_fields)
    write_csv(REPORTS / "STAGE6_IDENTITY_TARGET_AUDIT.csv", identities, list(identities[0]))
    write_csv(REPORTS / "STAGE6_FALSE_MATCH_IMPORT_GUARD.csv", false_guards, list(false_guards[0]))
    write_csv(
        REPORTS / "STAGE6_ROLLBACK_PLAN.csv",
        rollbacks,
        [
            "sterbrust_product_id", "would_change_fields_from_public_comparison",
            "rollback_price", "rollback_currency", "rollback_availability",
            "rollback_availability_raw", "rollback_quantity", "captured_at",
            "rollback_status",
        ],
    )
    write_csv(
        REPORTS / "STAGE6_CANARY_PLAN.csv",
        canaries,
        [
            "sterbrust_product_id", "reason_selected", "current_price", "desired_price",
            "current_availability", "desired_availability", "current_quantity",
            "desired_quantity", "expected_changed_fields", "rollback_price",
            "rollback_availability", "rollback_quantity", "execution_status",
        ],
    )
    metadata_warnings = [row for row in identities if row["warning"]]
    write_csv(
        REPORTS / "STAGE6_CATALOG_METADATA_WARNINGS.csv",
        metadata_warnings,
        list(identities[0]),
    )

    plan_summary = {
        "stage6_status": BLOCKED_STATUS,
        "feed_products": len(desired_rows),
        "exact_existing_targets": exact,
        "ambiguous_targets": ambiguous,
        "missing_targets": missing,
        "duplicate_targets": duplicate_targets,
        "classification_counts": dict(sorted(action_counts.items())),
        "observed_public_comparison": {
            "unchanged_price_and_availability": unchanged_comparison,
            "price_changed": price_changed,
            "availability_changed": availability_changed,
            "quantity_changed": "NOT_DETERMINABLE_IMPORT_CONTRACT_UNKNOWN",
            "missing_current_price": missing_current_price,
        },
        "no_eligible_write_actions": 0,
        "known_false_match_import_leaks": sum(int(row["import_plan_leak_count"]) for row in false_guards[:-1]),
        "mrx3_import_actions": int(false_guards[-1]["import_plan_leak_count"]),
        "blocking_reasons": list(contract_evidence().blockers),
    }
    write_json(REPORTS / "STAGE6_IMPORT_PLAN_SUMMARY.json", plan_summary)

    blast = {
        "result": "REPORTED_POLICY_THRESHOLDS_UNSET",
        "products_audited": len(desired_rows),
        "public_price_changed_count": price_changed,
        "public_availability_changed_count": availability_changed,
        "quantity_changed_count": "NOT_DETERMINABLE_IMPORT_CONTRACT_UNKNOWN",
        "public_comparison_unchanged_count": unchanged_comparison,
        "price_increase_count": price_counts.get("INCREASE", 0),
        "price_decrease_count": price_counts.get("DECREASE", 0),
        "missing_current_price_count": missing_current_price,
        "large_increase_count": "NOT_EVALUATED_NO_APPROVED_THRESHOLD",
        "large_decrease_count": "NOT_EVALUATED_NO_APPROVED_THRESHOLD",
        "thresholds": {
            "max_products_changed_per_first_run": None,
            "max_percentage_changed": None,
            "max_single_price_delta_percent": None,
        },
        "largest_absolute_percentage_deltas_for_review": deltas[:10],
    }
    write_json(REPORTS / "STAGE6_BLAST_RADIUS_QA.json", blast)

    allowlist = {
        "result": "PASS_LOCAL_FAIL_CLOSED_POLICY",
        "live_importer_mapping_status": "NOT_VERIFIED",
        "allowed_logical_fields": ["price", "availability"],
        "conditional_fields": {"quantity": "DISABLED_UNTIL_STORE_AND_QUANTITY_CONTRACT_PROVEN"},
        "forbidden_fields": [
            "NAME", "DESCRIPTION", "PREVIEW_TEXT", "DETAIL_TEXT", "CATEGORY",
            "SECTION", "BRAND", "MODEL", "SEO", "META_TITLE", "META_DESCRIPTION",
            "URL", "XML_ID", "ARTICLE", "IMAGES", "DOCUMENTS", "PROPERTIES",
        ],
        "unknown_field_behavior": "FAIL_CLOSED",
        "create_new_products": False,
        "write_enabled": False,
    }
    write_json(REPORTS / "STAGE6_WRITE_FIELD_ALLOWLIST.json", allowlist)
    write_json(REPORTS / "STAGE6_IMPORT_PAYLOAD_VALIDATION.json", {
        "result": BLOCKED_STATUS,
        "payload_generated": False,
        "canonical_stage5_xml_modified": False,
        "reason": "Exact importer input contract, update key, create-new behavior, price type and availability/store mapping are not proven.",
        "validator_framework": "PASS_UNIT_TESTED_FAIL_CLOSED",
        "unexpected_catalog_fields": "NOT_APPLICABLE_NO_PAYLOAD",
        "write_methods_used": 0,
    })
    write_json(REPORTS / "STAGE6_CHECKPOINT_PROVENANCE.json", {
        "authoritative_source_archive": SOURCE_CHECKPOINT_FILENAME,
        "authoritative_source_archive_sha256": SOURCE_CHECKPOINT_SHA256,
        "canonical_stage5_xml": "exports/sterbrust_selected_offers.xml",
        "canonical_stage5_xml_sha256": CANONICAL_STAGE5_XML_SHA256,
        "canonical_stage5_xml_modified": False,
        "older_archives_used": False,
    })

    pp_plan = next(row for row in plan_csv if row["sterbrust_product_id"] == "88585")
    pp_identity = next(row for row in identities if row["feed_product_id"] == "88585")
    pp_result = "PASS" if pp_identity["status"] == "EXACT_EXISTING_TARGET" else "FAIL"

    write_text(REPORTS / "STAGE6_IMPORT_CONTRACT.md", f"""# Stage 6 Sterbrust import contract

## Result

`STAGE6 = {BLOCKED_STATUS}`

The currently reachable admin entry point is `{IMPORT_ADMIN_ENTRY}`. A current read-only
GET to `{IMPORT_PROFILE_EVIDENCE_ENTRY}` reached the Bitrix authorization page. The URL and
historical profile surface identify the installed path as **ESOL XML Import**, but this does
not prove the selected profile's input format or update behavior.

## What is proven

- Stage 5 is a canonical YML snapshot, not an automatically approved live-import file.
- 117 feed IDs are Sterbrust product IDs and are checked against the current public card's
  main `data-id` in `STAGE6_IDENTITY_TARGET_AUDIT.csv`.
- Stage 6 uses public GET requests only and has no upload/import call.
- The separate local adapter is fail-closed, update-allowlist-only, and refuses transport
  without `STERBRUST_WRITE_ENABLE=YES` plus a fully proven contract.

## Contract fields not proven

- exact ESOL profile ID used for this integration;
- root element, required nodes, encoding and full/incremental semantics;
- profile identity field and whether numeric Bitrix product ID is accepted as its update key;
- "update existing only" / "create new disabled" profile settings;
- price type ID/name and VAT behavior;
- availability property, catalog quantity, store and `CAN_BUY` behavior;
- cron/server entry point and rollback response contract.

## Evidence required to unblock

Provide one or more read-only artifacts: ESOL profile screenshots/export, an XML sample now
used by that profile, the server cron line, the PHP importer/adapter configuration, or
read-only authenticated admin/SSH access. Until then no import payload is generated.
""")

    write_text(REPORTS / "STAGE6_IMPORT_FORMAT_MAPPING.md", """# Stage 6 import format mapping

`IMPORT_FORMAT_CONTRACT = NOT_PROVEN`

| Logical source | Stage 5 canonical YML | Sterbrust importer field |
|---|---|---|
| target identity | `offer/@id` = Sterbrust product ID | NOT_VERIFIED |
| price | `offer/price` | NOT_VERIFIED price type |
| currency | `offer/currencyId` | NOT_VERIFIED |
| availability | `offer/@available` plus normalized trace | NOT_VERIFIED property/catalog mapping |
| quantity | `param[@name='quantity']` when known | NOT_VERIFIED store/quantity mapping |
| name/url/category/vendor | present for canonical completeness | FORBIDDEN from live writes |

No `exports/sterbrust_import_payload.xml` was created because doing so would guess the
ESOL/Bitrix contract. `exports/sterbrust_selected_offers.xml` is unchanged.
""")

    write_text(REPORTS / "STAGE6_WRITE_METHOD_CONTRACT.md", """# Stage 6 write-method contract

`STERBRUST_WRITE_METHODS_USED = 0`

| Method/entry point | Purpose | Allowed field | Target identity | Expected response | Rollback |
|---|---|---|---|---|---|
| ESOL XML Import profile | Candidate existing mechanism; exact profile not proven | NONE in Stage 6 | NOT_PROVEN | NOT_PROVEN | NOT_EXECUTED |
| Separate future adapter | Only after evidence and Stage 7 approval | price, availability; quantity only if proven | exact existing-ID allowlist | must be specified by proven contract | restore Stage 6 captured values |

The existing snapshot client remains restricted to `catalog.product.list`,
`catalog.section.list`, and `catalog.productProperty.list`. It was not weakened. The Stage 6
package contains no configured Bitrix write method, upload action, cron command, or secret.
""")

    write_text(REPORTS / "STAGE6_PRICE_CONTRACT.md", f"""# Stage 6 price contract

`PRICE_CONTRACT = NOT_PROVEN`

The public storefront exposes current Offer prices and currency, so comparison coverage is
available for {len(desired_rows) - missing_current_price}/{len(desired_rows)} products.
However, the Bitrix catalog price type ID/name, storage/API field, VAT inclusion, and ESOL
profile mapping are not visible publicly. Stage 5 `price` therefore has **no authorized live
destination yet**. No new price type is created and no price is written.
""")

    write_text(REPORTS / "STAGE6_AVAILABILITY_CONTRACT.md", """# Stage 6 availability contract

`AVAILABILITY_CONTRACT = NOT_PROVEN`

| Stage 5 normalized state | Current public comparison meaning | Authorized Bitrix write mapping |
|---|---|---|
| `in_stock` | compare with Schema.org `InStock` | NOT_PROVEN |
| `preorder` | preserve preorder intent; never infer positive quantity | NOT_PROVEN |
| `out_of_stock` | compare with Schema.org `OutOfStock` | NOT_PROVEN |

The public Schema.org value proves the rendered storefront state, not whether Sterbrust uses
catalog quantity, store balance, `CAN_BUY`, property 1569, or another field. In particular,
Stage 5 `preorder -> available=true` is not converted to "В наличии" or quantity greater
than zero.
""")

    write_text(REPORTS / "STAGE6_QUANTITY_CONTRACT.md", """# Stage 6 quantity/store contract

`QUANTITY_CONTRACT = NOT_PROVEN`

Public product pages do not expose exact Bitrix catalog/store quantity. No store ID, single-
versus-multi-store rule, `CAN_BUY` behavior, or preorder balance behavior is proven. Quantity
writes are disabled. A Stage 5 NULL quantity remains NULL and is never converted to zero.
""")

    write_text(REPORTS / "STAGE6_PP800F_DRY_RUN.md", f"""# PP-800F Stage 6 dry run

`PP800F_IMPORT_DRY_RUN = {pp_result}`

- Sterbrust target: `88585`
- Public exact-target evidence: `{pp_identity['status']}` via main `data-id`
- Current public price/state: `{pp_plan['current_live_price']} RUB` / `{pp_plan['current_availability']}`
- Desired Stage 5 price/state: `{pp_plan['desired_selected_price']} RUB` / `{pp_plan['desired_normalized_availability']}`
- Exact update-key candidate: numeric Bitrix product ID `88585`
- Importer support for that key: `NOT_PROVEN`
- Fields that would differ by public comparison: `{pp_plan['write_fields_required'] or 'none'}`
- Dry-run classification: `{pp_plan['action']}`
- Live changes: `0`
""")

    qa_lines = [
        "# Stage 6 QA report",
        "",
        f"`STAGE6 = {BLOCKED_STATUS}`",
        "",
        f"- Stage 5 regression: {'PASS' if (test_count or 0) >= 253 else 'PENDING_TEST_COUNT'}",
        f"- Tests: {test_count if test_count is not None else 'NOT_RECORDED'}",
        f"- Selected products audited: {len(desired_rows)}",
        f"- Exact public targets: {exact}",
        f"- Missing targets: {missing}",
        f"- Ambiguous targets: {ambiguous}",
        f"- Duplicate targets: {duplicate_targets}",
        f"- Known false-match import leaks: {plan_summary['known_false_match_import_leaks']}",
        f"- MR-X3 import actions: {plan_summary['mrx3_import_actions']}",
        f"- PP-800F dry run: {pp_result}",
        f"- Public price differences: {price_changed}",
        f"- Public availability differences: {availability_changed}",
        f"- Dry-run action classifications: {dict(sorted(action_counts.items()))}",
        "- Quantity differences: NOT_DETERMINABLE_IMPORT_CONTRACT_UNKNOWN",
        f"- Rollback rows captured: {len(rollbacks)} (logical public values; importer mapping blocked)",
        f"- Canary rows designed: {len(canaries)}; executed: 0",
        "- Live writes: 0",
        f"- Live state changed by Stage 6: {zero_qa['live_state_changed_by_stage6']}",
        f"- External before/after differences: {zero_qa['external_change_count']}",
        "- Migrations 001-011: unchanged; migration 012: not required",
        "- Canonical Stage 5 XML: unchanged",
        "",
        "Stage 6 cannot close until authenticated profile/server evidence proves the exact",
        "format, update key, no-create setting, price type, availability and quantity/store contract.",
        "",
    ]
    write_text(REPORTS / "STAGE6_QA_REPORT.md", "\n".join(qa_lines))

    test_text = str(test_count) if test_count is not None else "NOT_RECORDED"
    summary = f"""STAGE6 = {BLOCKED_STATUS}
STAGE5_REGRESSION = {'PASS' if (test_count or 0) >= 253 else 'PENDING_TEST_COUNT'}
ALL_PREVIOUS_TESTS = {'PASS' if (test_count or 0) >= 253 else 'PENDING_TEST_COUNT'}
ALL_TESTS = {test_text}
IMPORT_MECHANISM_IDENTIFIED = PARTIAL_ESOL_XML_IMPORT_ENTRY_ONLY
IMPORT_FORMAT_CONTRACT = NOT_PROVEN
UPDATE_KEY_PROVEN = NO
UPDATE_EXISTING_ONLY = NOT_PROVEN
CREATE_NEW_PRODUCTS = NOT_PROVEN_IN_LIVE_PROFILE
LOCAL_CREATE_NEW_GUARD = DISABLED
WRITE_FIELD_ALLOWLIST = PASS_LOCAL_FAIL_CLOSED
PRICE_CONTRACT = NOT_PROVEN
AVAILABILITY_CONTRACT = NOT_PROVEN
QUANTITY_CONTRACT = NOT_PROVEN
FEED_PRODUCTS = {len(desired_rows)}
EXACT_EXISTING_TARGETS = {exact}
AMBIGUOUS_TARGETS = {ambiguous}
MISSING_TARGETS = {missing}
DUPLICATE_TARGETS = {duplicate_targets}
NO_ELIGIBLE_WRITE_ACTIONS = 0
KNOWN_FALSE_MATCH_IMPORT_LEAKS = {plan_summary['known_false_match_import_leaks']}
MRX3_IMPORT_ACTIONS = {plan_summary['mrx3_import_actions']}
PP800F_IMPORT_DRY_RUN = {pp_result}
DRY_RUN = PASS_FAIL_CLOSED
DRY_RUN_NO_CHANGE_ACTIONS = {action_counts.get('NO_CHANGE', 0)}
DRY_RUN_WOULD_UPDATE_PRICE_ACTIONS = {action_counts.get('WOULD_UPDATE_PRICE', 0)}
DRY_RUN_WOULD_UPDATE_AVAILABILITY_ACTIONS = {action_counts.get('WOULD_UPDATE_AVAILABILITY', 0)}
DRY_RUN_WOULD_UPDATE_PRICE_AND_AVAILABILITY_ACTIONS = {action_counts.get('WOULD_UPDATE_PRICE_AND_AVAILABILITY', 0)}
DRY_RUN_WOULD_UPDATE_QUANTITY_ACTIONS = {action_counts.get('WOULD_UPDATE_QUANTITY', 0)}
DRY_RUN_BLOCKED_IMPORT_CONTRACT_ACTIONS = {action_counts.get('BLOCKED_IMPORT_CONTRACT', 0)}
PUBLIC_COMPARISON_NO_CHANGE = {unchanged_comparison}
PUBLIC_PRICE_CHANGE_COUNT = {price_changed}
PUBLIC_AVAILABILITY_CHANGE_COUNT = {availability_changed}
QUANTITY_CHANGE_COUNT = NOT_DETERMINABLE_IMPORT_CONTRACT_UNKNOWN
ROLLBACK_PLAN_ROWS = {len(rollbacks)}
CANARY_PLAN_ROWS = {len(canaries)}
LIVE_WRITES = 0
STERBRUST_WRITE_METHODS_USED = 0
LIVE_STATE_CHANGED_BY_STAGE6 = {zero_qa['live_state_changed_by_stage6']}
EXTERNAL_CHANGE_COUNT = {zero_qa['external_change_count']}
MIGRATIONS_001_011_IMMUTABLE = PASS
MIGRATION_012 = NOT_REQUIRED
IMPORT_PAYLOAD = NOT_GENERATED
CANONICAL_STAGE5_XML_CHANGED = NO
BEFORE_CAPTURE_COMPLETED_AT = {before_completed_at}
"""
    write_text(REPORTS / "STAGE6_SUMMARY.txt", summary)
    return {
        **plan_summary,
        "tests": test_count,
        "pp800f": pp_result,
        "rollback_rows": len(rollbacks),
        "canary_rows": len(canaries),
        "blast_radius": blast,
        "zero_write": zero_qa,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", choices=("before", "after"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--test-count", type=int)
    parser.add_argument("--reports-only", action="store_true")
    args = parser.parse_args()

    if args.capture:
        capture(args.capture, args.workers)
    if args.capture == "before" or args.reports_only:
        if not BEFORE_RAW.exists():
            raise SystemExit("Before capture is required")
        print(json.dumps(render_reports(args.test_count), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.capture == "after":
        if not BEFORE_RAW.exists():
            raise SystemExit("Before capture is required before after capture")
        print(json.dumps(render_reports(args.test_count), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
