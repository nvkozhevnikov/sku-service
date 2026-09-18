from __future__ import annotations

from decimal import Decimal

import pytest

from universal_supplier.sterbrust_import_contract import (
    ContractValidationError,
    CountingTransport,
    CurrentCommercialState,
    DesiredCommercialState,
    ImportContractEvidence,
    ProvenXmlContract,
    SterbrustWriteAdapter,
    WriteRefused,
    compare_state,
    dry_run,
    normalize_public_availability,
    serialize_import_payload,
    validate_managed_rows,
    validate_price,
    validate_target_mapping,
    validate_write_fields,
)


def evidence(**overrides) -> ImportContractEvidence:
    values = {
        "mechanism": "fixture",
        "admin_entry_point": "fixture",
        "format_verified": True,
        "update_key_verified": True,
        "update_existing_only_verified": True,
        "create_new_disabled_verified": True,
        "price_contract_verified": True,
        "availability_contract_verified": True,
        "quantity_contract_verified_or_not_required": True,
    }
    values.update(overrides)
    return ImportContractEvidence(**values)


def desired(**overrides) -> DesiredCommercialState:
    values = {
        "sterbrust_product_id": "88585",
        "name": "Partner PP-800F",
        "article": "508002",
        "price": Decimal("339940"),
        "currency": "RUB",
        "availability": "in_stock",
        "quantity": Decimal("1"),
        "supplier_code": "partner_st",
        "supplier_external_id": "297",
        "supplier_sku": "508002",
        "url": "https://sterbrust.com/catalog/pp-800f/",
    }
    values.update(overrides)
    return DesiredCommercialState(**values)


def current(**overrides) -> CurrentCommercialState:
    values = {
        "sterbrust_product_id": "88585",
        "name": "Partner PP-800F",
        "article": "508002",
        "price": Decimal("339940"),
        "currency": "RUB",
        "availability": "in_stock",
        "availability_raw": "https://schema.org/InStock",
        "quantity": None,
        "target_match_count": 1,
        "observed_at": "2026-09-09T00:00:00+00:00",
        "evidence_source": "public_product_json_ld_and_main_data_id",
        "error": "",
    }
    values.update(overrides)
    return CurrentCommercialState(**values)


def xml_contract(**overrides) -> ProvenXmlContract:
    values = {
        "root_element": "updates",
        "update_element": "product",
        "identity_element": "existingId",
        "field_elements": {"price": "price", "availability": "availability", "quantity": "quantity"},
        "availability_values": {"in_stock": "stock", "preorder": "preorder", "out_of_stock": "none"},
    }
    values.update(overrides)
    return ProvenXmlContract(**values)


def test_unproven_contract_lists_every_blocker():
    value = ImportContractEvidence("ESOL", "esol_import_xml.php")
    assert not value.ready_for_payload
    assert len(value.blockers) == 7


def test_fully_proven_fixture_contract_is_ready():
    assert evidence().ready_for_payload


def test_import_key_uniqueness_passes():
    validate_target_mapping(["1", "2"], {"1": 1, "2": 1})


def test_missing_target_fails():
    with pytest.raises(ContractValidationError, match="MISSING_TARGET:2"):
        validate_target_mapping(["1", "2"], {"1": 1})


def test_ambiguous_target_fails():
    with pytest.raises(ContractValidationError, match="AMBIGUOUS_TARGET:1:2"):
        validate_target_mapping(["1"], {"1": 2})


def test_duplicate_target_fails():
    with pytest.raises(ContractValidationError, match="DUPLICATE_TARGET:1"):
        validate_target_mapping(["1", "1"], {"1": 1})


def test_write_allowlist_accepts_minimal_commercial_fields():
    assert validate_write_fields(["price", "availability"]) == ("availability", "price")


@pytest.mark.parametrize("field", ["name", "category", "description", "seo"])
def test_catalog_content_cannot_be_written(field):
    with pytest.raises(ContractValidationError, match=f"WRITE_FIELD_NOT_ALLOWED:{field}"):
        validate_write_fields([field])


def test_quantity_requires_proven_contract():
    with pytest.raises(ContractValidationError, match="WRITE_FIELD_NOT_ALLOWED:quantity"):
        validate_write_fields(["quantity"])
    assert validate_write_fields(["quantity"], quantity_contract_proven=True) == ("quantity",)


def test_price_mapping_requires_positive_rub():
    assert validate_price(Decimal("1.01"), "RUB") == Decimal("1.01")
    with pytest.raises(ContractValidationError, match="NON_POSITIVE_PRICE"):
        validate_price(Decimal("0"), "RUB")
    with pytest.raises(ContractValidationError, match="UNSUPPORTED_CURRENCY:EUR"):
        validate_price(Decimal("1"), "EUR")


@pytest.mark.parametrize("raw,expected", [
    ("https://schema.org/InStock", "in_stock"),
    ("OutOfStock", "out_of_stock"),
    ("PreOrder", "preorder"),
    ("BackOrder", "backorder"),
])
def test_public_availability_mapping(raw, expected):
    assert normalize_public_availability(raw) == expected


def test_null_quantity_is_not_converted_to_zero():
    payload = serialize_import_payload([desired(quantity=None)], evidence(), xml_contract())
    assert b"<quantity>" not in payload
    assert b">0</quantity>" not in payload


def test_no_eligible_row_is_excluded():
    row = desired(supplier_external_id="blocked")
    with pytest.raises(ContractValidationError, match="NO_ELIGIBLE_ROW_INCLUDED"):
        validate_managed_rows([row], {("partner_st", "blocked")})


@pytest.mark.parametrize("supplier,external_id,target", [
    ("partner_st", "1655", "61421"),
    ("partner_st", "1656", "40495"),
    ("partner_st", "1835", "96887"),
    ("partner_st", "1837", "96887"),
    ("optimum", "561", "18451"),
    ("optimum", "782", "19862"),
])
def test_known_false_matches_are_excluded(supplier, external_id, target):
    row = desired(supplier_code=supplier, supplier_external_id=external_id, sterbrust_product_id=target)
    with pytest.raises(ContractValidationError, match="KNOWN_FALSE_MATCH_LEAK"):
        validate_managed_rows([row], set())


def test_mrx3_is_excluded():
    row = desired(supplier_external_id="305")
    with pytest.raises(ContractValidationError, match="MRX3_IMPORT_ACTION"):
        validate_managed_rows([row], set())


def test_pp800f_target_exact_and_no_change_with_proven_fixture():
    plan = compare_state(desired(), current(), evidence())
    assert plan.sterbrust_product_id == "88585"
    assert plan.identity_confidence == "EXACT_PUBLIC_DATA_ID"
    assert plan.action == "NO_CHANGE"


def test_unproven_import_contract_blocks_even_when_commercial_state_matches():
    plan = compare_state(desired(), current(), ImportContractEvidence("ESOL", "esol_import_xml.php"))
    assert plan.action == "BLOCKED_IMPORT_CONTRACT"
    assert "UPDATE_KEY_NOT_PROVEN" in plan.blocking_reason


def test_missing_identity_overrides_import_contract_block():
    plan = compare_state(desired(), current(target_match_count=0), ImportContractEvidence("ESOL", "entry"))
    assert plan.action == "BLOCKED_IDENTITY"
    assert plan.blocking_reason == "MISSING_TARGET"


def test_dry_run_never_calls_write_transport():
    transport = CountingTransport()
    result = dry_run(b"payload", transport)
    assert result["write_calls"] == 0
    assert transport.calls == 0


def test_write_transport_refuses_without_explicit_enable_gate(monkeypatch):
    monkeypatch.delenv("STERBRUST_WRITE_ENABLE", raising=False)
    transport = CountingTransport()
    adapter = SterbrustWriteAdapter(transport, evidence(), ["88585"])
    with pytest.raises(WriteRefused, match="exactly YES"):
        adapter.execute(b"payload", ["88585"])
    assert transport.calls == 0


def test_write_transport_refuses_when_contract_unproven(monkeypatch):
    monkeypatch.setenv("STERBRUST_WRITE_ENABLE", "YES")
    transport = CountingTransport()
    adapter = SterbrustWriteAdapter(transport, ImportContractEvidence("ESOL", "entry"), ["88585"])
    with pytest.raises(WriteRefused, match="not proven"):
        adapter.execute(b"payload", ["88585"])
    assert transport.calls == 0


def test_write_transport_refuses_target_outside_existing_id_allowlist(monkeypatch):
    monkeypatch.setenv("STERBRUST_WRITE_ENABLE", "YES")
    transport = CountingTransport()
    adapter = SterbrustWriteAdapter(transport, evidence(), ["88585"])
    with pytest.raises(WriteRefused, match="outside"):
        adapter.execute(b"payload", ["99999"])
    assert transport.calls == 0


def test_import_payload_is_deterministic_for_supplied_proven_contract_fixture():
    rows = [desired(sterbrust_product_id="2"), desired(sterbrust_product_id="1")]
    first = serialize_import_payload(rows, evidence(), xml_contract())
    second = serialize_import_payload(list(reversed(rows)), evidence(), xml_contract())
    assert first == second
    assert first.index(b"<existingId>1</existingId>") < first.index(b"<existingId>2</existingId>")


def test_no_create_guarantee_is_serialized_for_proven_contract_fixture():
    payload = serialize_import_payload([desired()], evidence(), xml_contract())
    assert b'<updates createNew="false">' in payload


def test_serializer_rejects_contract_that_allows_create():
    with pytest.raises(ContractValidationError, match="CREATE_NEW_NOT_DISABLED"):
        serialize_import_payload([desired()], evidence(), xml_contract(create_new_disabled_value="true"))


def test_serializer_refuses_without_complete_contract_evidence():
    with pytest.raises(ContractValidationError, match="IMPORT_FORMAT_CONTRACT_NOT_PROVEN"):
        serialize_import_payload([desired()], ImportContractEvidence("ESOL", "entry"), xml_contract())


def test_serializer_rejects_unmapped_availability():
    with pytest.raises(ContractValidationError, match="UNMAPPED_AVAILABILITY:incoming"):
        serialize_import_payload([desired(availability="incoming")], evidence(), xml_contract())


def test_rollback_source_state_is_complete_for_each_observed_change():
    plan = compare_state(desired(price=Decimal("350000")), current(), evidence())
    assert plan.action == "WOULD_UPDATE_PRICE"
    assert plan.current_price == Decimal("339940")
    assert plan.price_delta == Decimal("10060")
