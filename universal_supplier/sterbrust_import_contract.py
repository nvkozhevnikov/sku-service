"""Fail-closed Sterbrust import-contract primitives for Stage 6.

This module deliberately does not contain a Sterbrust write method or a guessed
Bitrix field mapping.  A payload can only be serialized when an explicit,
externally proven contract is supplied by the caller.  Stage 6 itself uses the
dry-run plan and validation helpers only.
"""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Protocol, Sequence


DRY_RUN_STATUSES = frozenset({
    "NO_CHANGE",
    "WOULD_UPDATE_PRICE",
    "WOULD_UPDATE_AVAILABILITY",
    "WOULD_UPDATE_PRICE_AND_AVAILABILITY",
    "WOULD_UPDATE_QUANTITY",
    "BLOCKED_IDENTITY",
    "BLOCKED_IMPORT_CONTRACT",
    "BLOCKED_UNSUPPORTED_STATE",
    "NOT_MANAGED",
})

ALLOWED_LOGICAL_WRITE_FIELDS = frozenset({"price", "availability"})
CONDITIONAL_LOGICAL_WRITE_FIELDS = frozenset({"quantity"})
FORBIDDEN_CATALOG_FIELDS = frozenset({
    "name", "description", "preview_text", "detail_text", "category",
    "section", "brand", "model", "seo", "meta_title",
    "meta_description", "url", "xml_id", "article", "images",
    "documents", "properties", "old_price",
})

KNOWN_FALSE_MATCH_PAIRS = (
    ("partner_st", "1655", "61421", "LX20 PRO must not target LX20 NEW"),
    ("partner_st", "1656", "40495", "PP-13D NEW must not target PP-13D"),
    ("partner_st", "1835", "96887", "ETM-16U controller must not target manipulator"),
    ("partner_st", "1837", "96887", "ETM-16U handle must not target manipulator"),
    ("optimum", "561", "18451", "HCV125 jaws must not target HCV125 vise"),
    ("optimum", "782", "19862", "TU2304V must not target TU2304"),
)


class ContractValidationError(ValueError):
    """Fail-closed validation error with stable machine-readable violations."""

    def __init__(self, violations: Iterable[str]):
        self.violations = tuple(str(item) for item in violations)
        super().__init__("; ".join(self.violations))


class WriteRefused(RuntimeError):
    """Raised before any transport call when the production gate is closed."""


@dataclass(frozen=True)
class ImportContractEvidence:
    mechanism: str
    admin_entry_point: str
    import_profile_id: str | None = None
    format_verified: bool = False
    update_key_verified: bool = False
    update_existing_only_verified: bool = False
    create_new_disabled_verified: bool = False
    price_contract_verified: bool = False
    availability_contract_verified: bool = False
    quantity_contract_verified_or_not_required: bool = False
    source_evidence: str = ""

    @property
    def ready_for_payload(self) -> bool:
        return all((
            self.format_verified,
            self.update_key_verified,
            self.update_existing_only_verified,
            self.create_new_disabled_verified,
            self.price_contract_verified,
            self.availability_contract_verified,
            self.quantity_contract_verified_or_not_required,
        ))

    @property
    def blockers(self) -> tuple[str, ...]:
        checks = (
            (self.format_verified, "IMPORT_FORMAT_CONTRACT_NOT_PROVEN"),
            (self.update_key_verified, "UPDATE_KEY_NOT_PROVEN"),
            (self.update_existing_only_verified, "UPDATE_EXISTING_ONLY_NOT_PROVEN"),
            (self.create_new_disabled_verified, "CREATE_NEW_DISABLED_NOT_PROVEN"),
            (self.price_contract_verified, "PRICE_CONTRACT_NOT_PROVEN"),
            (self.availability_contract_verified, "AVAILABILITY_CONTRACT_NOT_PROVEN"),
            (self.quantity_contract_verified_or_not_required, "QUANTITY_CONTRACT_NOT_PROVEN"),
        )
        return tuple(code for passed, code in checks if not passed)


@dataclass(frozen=True)
class ProvenXmlContract:
    """Exact serializer settings copied from independently proven evidence.

    Stage 6 must not instantiate this from guesses.  The generic serializer is
    present so a later evidence-backed adapter can be tested without weakening
    the read-only snapshot client.
    """

    root_element: str
    update_element: str
    identity_element: str
    field_elements: Mapping[str, str]
    availability_values: Mapping[str, str]
    currency_value: str = "RUB"
    create_new_attribute: str = "createNew"
    create_new_disabled_value: str = "false"


@dataclass(frozen=True)
class DesiredCommercialState:
    sterbrust_product_id: str
    name: str
    article: str
    price: Decimal
    currency: str
    availability: str
    quantity: Decimal | None
    supplier_code: str
    supplier_external_id: str
    supplier_sku: str
    url: str


@dataclass(frozen=True)
class CurrentCommercialState:
    sterbrust_product_id: str
    name: str
    article: str
    price: Decimal | None
    currency: str | None
    availability: str | None
    availability_raw: str | None
    quantity: Decimal | None
    target_match_count: int
    observed_at: str
    evidence_source: str
    error: str = ""


@dataclass(frozen=True)
class DryRunPlanRow:
    sterbrust_product_id: str
    current_price: Decimal | None
    desired_price: Decimal
    price_change: str
    price_delta: Decimal | None
    price_delta_percent: Decimal | None
    current_availability: str | None
    desired_availability: str
    availability_change: str
    current_quantity: Decimal | None
    desired_quantity: Decimal | None
    write_fields_required: tuple[str, ...]
    identity_confidence: str
    action: str
    blocking_reason: str


class WriteTransport(Protocol):
    def send(self, payload: bytes) -> Any: ...


class CountingTransport:
    """Test transport.  Stage 6 dry-run never calls ``send``."""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, payload: bytes) -> dict[str, Any]:
        self.calls += 1
        return {"accepted": True, "bytes": len(payload)}


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractValidationError([f"INVALID_DECIMAL:{value}"]) from exc


def decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def normalize_public_availability(value: str | None) -> str | None:
    if not value:
        return None
    token = str(value).rsplit("/", 1)[-1].casefold()
    mapping = {
        "instock": "in_stock",
        "outofstock": "out_of_stock",
        "preorder": "preorder",
        "backorder": "backorder",
        "discontinued": "discontinued",
        "limitedavailability": "in_stock",
    }
    return mapping.get(token)


def validate_target_mapping(expected_ids: Sequence[str], observed_match_counts: Mapping[str, int]) -> None:
    violations: list[str] = []
    duplicates = [value for value, count in Counter(map(str, expected_ids)).items() if count != 1]
    violations.extend(f"DUPLICATE_TARGET:{value}" for value in sorted(duplicates))
    for target in sorted(map(str, expected_ids), key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else 0, value)):
        count = int(observed_match_counts.get(target, 0))
        if count == 0:
            violations.append(f"MISSING_TARGET:{target}")
        elif count != 1:
            violations.append(f"AMBIGUOUS_TARGET:{target}:{count}")
    if violations:
        raise ContractValidationError(violations)


def validate_write_fields(fields: Iterable[str], *, quantity_contract_proven: bool = False) -> tuple[str, ...]:
    normalized = tuple(sorted({str(field).strip().casefold() for field in fields if str(field).strip()}))
    allowed = set(ALLOWED_LOGICAL_WRITE_FIELDS)
    if quantity_contract_proven:
        allowed.update(CONDITIONAL_LOGICAL_WRITE_FIELDS)
    violations = [f"WRITE_FIELD_NOT_ALLOWED:{field}" for field in normalized if field not in allowed]
    if violations:
        raise ContractValidationError(violations)
    return normalized


def validate_price(price: Decimal | None, currency: str) -> Decimal:
    violations: list[str] = []
    if price is None:
        violations.append("NULL_PRICE")
    elif price <= 0:
        violations.append(f"NON_POSITIVE_PRICE:{decimal_text(price)}")
    if currency != "RUB":
        violations.append(f"UNSUPPORTED_CURRENCY:{currency}")
    if violations:
        raise ContractValidationError(violations)
    assert price is not None
    return price


def compare_state(
    desired: DesiredCommercialState,
    current: CurrentCommercialState,
    contract: ImportContractEvidence,
) -> DryRunPlanRow:
    if current.price is None:
        price_change = "UNKNOWN_CURRENT"
        price_delta = None
        price_delta_percent = None
    else:
        price_delta = desired.price - current.price
        if price_delta == 0:
            price_change = "UNCHANGED"
        elif price_delta > 0:
            price_change = "INCREASE"
        else:
            price_change = "DECREASE"
        price_delta_percent = (
            (price_delta / current.price * Decimal("100")) if current.price != 0 else None
        )

    if current.availability is None:
        availability_change = "UNKNOWN_CURRENT"
    elif current.availability == desired.availability:
        availability_change = "UNCHANGED"
    else:
        availability_change = "CHANGE"

    fields: list[str] = []
    if price_change != "UNCHANGED":
        fields.append("price")
    if availability_change != "UNCHANGED":
        fields.append("availability")
    # Quantity is intentionally omitted while the live store contract is unknown.

    if current.target_match_count != 1:
        action = "BLOCKED_IDENTITY"
        blocking = "MISSING_TARGET" if current.target_match_count == 0 else "AMBIGUOUS_TARGET"
        confidence = "UNPROVEN"
    elif current.error:
        action = "BLOCKED_IDENTITY"
        blocking = current.error
        confidence = "UNPROVEN"
    elif not contract.ready_for_payload:
        action = "BLOCKED_IMPORT_CONTRACT"
        blocking = "|".join(contract.blockers)
        confidence = "EXACT_PUBLIC_DATA_ID"
    elif current.availability is None:
        action = "BLOCKED_UNSUPPORTED_STATE"
        blocking = "CURRENT_AVAILABILITY_NOT_MAPPABLE"
        confidence = "EXACT_PUBLIC_DATA_ID"
    elif not fields:
        action = "NO_CHANGE"
        blocking = ""
        confidence = "EXACT_PUBLIC_DATA_ID"
    elif fields == ["price"]:
        action = "WOULD_UPDATE_PRICE"
        blocking = ""
        confidence = "EXACT_PUBLIC_DATA_ID"
    elif fields == ["availability"]:
        action = "WOULD_UPDATE_AVAILABILITY"
        blocking = ""
        confidence = "EXACT_PUBLIC_DATA_ID"
    else:
        action = "WOULD_UPDATE_PRICE_AND_AVAILABILITY"
        blocking = ""
        confidence = "EXACT_PUBLIC_DATA_ID"

    assert action in DRY_RUN_STATUSES
    return DryRunPlanRow(
        sterbrust_product_id=desired.sterbrust_product_id,
        current_price=current.price,
        desired_price=desired.price,
        price_change=price_change,
        price_delta=price_delta,
        price_delta_percent=price_delta_percent,
        current_availability=current.availability,
        desired_availability=desired.availability,
        availability_change=availability_change,
        current_quantity=current.quantity,
        desired_quantity=desired.quantity,
        write_fields_required=tuple(fields),
        identity_confidence=confidence,
        action=action,
        blocking_reason=blocking,
    )


def validate_managed_rows(
    desired_rows: Sequence[DesiredCommercialState],
    no_eligible_supplier_keys: Iterable[tuple[str, str]],
) -> None:
    violations: list[str] = []
    target_counts = Counter(row.sterbrust_product_id for row in desired_rows)
    violations.extend(
        f"DUPLICATE_TARGET:{target}:{count}"
        for target, count in sorted(target_counts.items()) if count != 1
    )
    no_eligible = set(no_eligible_supplier_keys)
    for row in desired_rows:
        validate_price(row.price, row.currency)
        if (row.supplier_code, row.supplier_external_id) in no_eligible:
            violations.append(f"NO_ELIGIBLE_ROW_INCLUDED:{row.supplier_code}:{row.supplier_external_id}")
    for supplier, external_id, prohibited_target, _reason in KNOWN_FALSE_MATCH_PAIRS:
        if any(
            row.supplier_code == supplier
            and row.supplier_external_id == external_id
            and row.sterbrust_product_id == prohibited_target
            for row in desired_rows
        ):
            violations.append(f"KNOWN_FALSE_MATCH_LEAK:{supplier}:{external_id}:{prohibited_target}")
    if any(row.supplier_code == "partner_st" and row.supplier_external_id == "305" for row in desired_rows):
        violations.append("MRX3_IMPORT_ACTION")
    if violations:
        raise ContractValidationError(violations)


def serialize_import_payload(
    rows: Sequence[DesiredCommercialState],
    contract_evidence: ImportContractEvidence,
    xml_contract: ProvenXmlContract | None,
) -> bytes:
    """Serialize deterministically only after every contract gate is proven."""

    if not contract_evidence.ready_for_payload:
        raise ContractValidationError(contract_evidence.blockers)
    if xml_contract is None:
        raise ContractValidationError(["MISSING_PROVEN_XML_CONTRACT"])
    validate_write_fields(xml_contract.field_elements.keys(), quantity_contract_proven=True)
    if xml_contract.create_new_disabled_value.casefold() not in {"false", "no", "0", "n"}:
        raise ContractValidationError(["CREATE_NEW_NOT_DISABLED"])

    root = ET.Element(xml_contract.root_element, {
        xml_contract.create_new_attribute: xml_contract.create_new_disabled_value,
    })
    for row in sorted(rows, key=lambda item: (not item.sterbrust_product_id.isdigit(), int(item.sterbrust_product_id) if item.sterbrust_product_id.isdigit() else 0, item.sterbrust_product_id)):
        validate_price(row.price, row.currency)
        if row.availability not in xml_contract.availability_values:
            raise ContractValidationError([f"UNMAPPED_AVAILABILITY:{row.availability}"])
        update = ET.SubElement(root, xml_contract.update_element)
        ET.SubElement(update, xml_contract.identity_element).text = row.sterbrust_product_id
        values: dict[str, str | None] = {
            "price": decimal_text(row.price),
            "availability": xml_contract.availability_values[row.availability],
            "quantity": decimal_text(row.quantity) if row.quantity is not None else None,
        }
        for logical_field in sorted(xml_contract.field_elements):
            value = values.get(logical_field)
            if value is not None:
                ET.SubElement(update, xml_contract.field_elements[logical_field]).text = value
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True) + b"\n"


def payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def dry_run(payload: bytes, transport: WriteTransport) -> dict[str, Any]:
    """Return payload diagnostics without invoking the supplied transport."""

    return {
        "dry_run": True,
        "write_calls": 0,
        "payload_bytes": len(payload),
        "payload_sha256": payload_sha256(payload),
        "transport_calls": int(getattr(transport, "calls", 0)),
    }


class SterbrustWriteAdapter:
    """Separate future write adapter with two independent fail-closed gates."""

    def __init__(
        self,
        transport: WriteTransport,
        contract_evidence: ImportContractEvidence,
        allowed_target_ids: Iterable[str],
    ) -> None:
        self.transport = transport
        self.contract_evidence = contract_evidence
        self.allowed_target_ids = frozenset(map(str, allowed_target_ids))

    def execute(self, payload: bytes, target_ids: Iterable[str]) -> Any:
        if os.environ.get("STERBRUST_WRITE_ENABLE", "NO") != "YES":
            raise WriteRefused("STERBRUST_WRITE_ENABLE must be exactly YES")
        if not self.contract_evidence.ready_for_payload:
            raise WriteRefused("Sterbrust import contract is not proven")
        targets = tuple(map(str, target_ids))
        if len(targets) != len(set(targets)):
            raise WriteRefused("Duplicate target ID")
        if not set(targets).issubset(self.allowed_target_ids):
            raise WriteRefused("Target outside explicit existing-ID allowlist")
        return self.transport.send(payload)


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
