"""Security review 2026-10, group 3: manual mapping authority, import contract, commercial parsing."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import time

import pytest

from universal_supplier.commercial import _identity_matches, _json_ld, _microdata, _parse_number
from universal_supplier.identity_service import _registry_rows, classify_identity_card
from universal_supplier.matching_policy_v2 import title_represents
from universal_supplier.matching_service import RegistryContext, match_cards
from universal_supplier.models import ProductCard
from universal_supplier.postgres import PostgresRepository
from universal_supplier.sterbrust_import_contract import (
    KNOWN_FALSE_MATCH_PAIRS, ContractValidationError, CountingTransport, SterbrustWriteAdapter,
    WriteRefused, load_safety_config, quantity_write_allowed, serialize_import_payload,
)
from universal_supplier.xml_export import FALSE_MATCH_GUARDS
from tests.test_stage6 import desired, evidence, xml_contract

ROOT = Path(__file__).resolve().parents[1]


# --- 7. Manual mapping authority ----------------------------------------------------

def _jet_card() -> ProductCard:
    return ProductCard(
        supplier_code="partner_st", external_id="920", sku="BD-920W", name="Токарный станок JET BD-920W",
        requested_url="https://partner-st.ru/p", final_url="https://partner-st.ru/p",
        canonical_url="https://partner-st.ru/p", http_status=200, redirect_urls=(), price=None, old_price=None,
        currency="RUB", availability_raw="", availability_normalized="unknown", quantity=None,
        description_text="", description_html="", brand="JET",
    )


def _jet_registry() -> RegistryContext:
    row = {"sterbrust_product_id": "200", "name": "Токарный станок JET BD-920W", "brand": "JET", "model": "BD-920W",
           "article": "", "category": "Станки", "properties": {}, "supplier_identifiers": defaultdict(list)}
    return RegistryContext(products={"200": row}, identifier_index={}, by_model={"bd920w": ["200"]},
                           by_brand={"jet": ["200"]}, title_identifier_index={})


def test_manual_link_to_deactivated_target_is_never_rematched_automatically():
    existing = {"920": {"sterbrust_product_id": "100", "manual_or_human_confirmed": True,
                        "origin_match_method": "EXISTING_LINK_MANUAL"}}
    row = match_cards([_jet_card()], _jet_registry(), existing)[0]
    assert row["status"] != "HIGH_CONFIDENCE_MATCH" and row["auto_accepted"] == "FALSE"
    assert (row["status"], row["match_method"], row["sterbrust_product_id"]) == (
        "REVIEW", "REVIEW_MANUAL_TARGET_CHANGE", "100")
    assert json.loads(row["evidence"])["manual_mapping_guard"]["manual_sterbrust_product_id"] == "100"
    # Without the manual link the same card does match 200: the guard is what stops it.
    assert match_cards([_jet_card()], _jet_registry(), {})[0]["sterbrust_product_id"] == "200"

    registry = _registry_rows(_jet_registry())
    audit = classify_identity_card(_jet_card().as_jsonable(), row, registry)
    assert audit["decision"] not in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"}
    assert audit["auto_accepted"] == "FALSE"


def test_manual_link_to_active_target_still_wins():
    existing = {"920": {"sterbrust_product_id": "200", "manual_or_human_confirmed": True}}
    row = match_cards([_jet_card()], _jet_registry(), existing)[0]
    assert (row["match_method"], row["sterbrust_product_id"]) == ("EXISTING_LINK_MANUAL", "200")


class _ScriptedCursor:
    def __init__(self, answers): self.answers = list(answers); self.sql = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None): self.sql.append(sql)
    def fetchone(self): return self.answers.pop(0)
    def fetchall(self): return []


class _ScriptedConnection:
    def __init__(self, cursor): self._cursor = cursor
    @contextmanager
    def transaction(self): yield
    def cursor(self): return self._cursor


def test_persist_match_decision_returns_the_guarded_effective_decision():
    cursor = _ScriptedCursor([
        (10, 5),     # source row: currently linked to catalog 5
        (7,),        # proposed target resolves to catalog 7
        (True,),     # effective manual mapping protects catalog 5
        None,        # no current product_matches row
    ])
    repository = PostgresRepository.__new__(PostgresRepository)
    repository.connection = _ScriptedConnection(cursor)
    decision = {"source_external_id": "920", "status": "HIGH_CONFIDENCE_MATCH", "match_method": "BRAND_MODEL",
                "confidence_score": 0.9, "sterbrust_product_id": "200", "auto_accepted": "TRUE",
                "evidence": "{}", "warnings": "{}", "conflicts": "{}"}
    outcome, effective = repository.persist_match_decision(decision, "2026-10-20T00:00:00+00:00")
    assert outcome == "INSERTED_EVENT"
    assert (effective["status"], effective["match_method"], effective["auto_accepted"]) == (
        "REVIEW", "REVIEW_MANUAL_TARGET_CHANGE", "FALSE")
    assert effective["conflict_class"] == "MANUAL_MAPPING_OVERRIDE_BLOCKED"
    assert "manual_mapping_guard" in json.loads(effective["evidence"])


def test_identity_reconcile_classifies_the_persisted_decision():
    source = (ROOT / "universal_supplier/identity_service.py").read_text(encoding="utf-8")
    assert "outcome, effective = repository.persist_match_decision(" in source
    assert "classify_identity_card(card.as_jsonable(), effective," in source


# --- 8. Import contract --------------------------------------------------------------

QUANTITY_FIELDS = {"price": "price", "availability": "availability", "quantity": "quantity"}


@pytest.mark.parametrize("verified,enabled", [(False, True), (True, False), (False, False)])
def test_quantity_field_requires_contract_flag_and_safety_config(verified, enabled):
    with pytest.raises(ContractValidationError, match="WRITE_FIELD_NOT_ALLOWED:quantity"):
        serialize_import_payload([desired()], evidence(quantity_contract_verified=verified),
                                 xml_contract(field_elements=QUANTITY_FIELDS),
                                 safety_config={"quantity_write_enabled": enabled})


def test_repository_safety_config_keeps_quantity_disabled():
    assert load_safety_config()["quantity_write_enabled"] is False
    assert not quantity_write_allowed(evidence(quantity_contract_verified=True))
    assert not quantity_write_allowed(evidence(quantity_contract_verified=True), {"quantity_write_enabled": "true"})


@pytest.mark.parametrize("supplier,external_id,target,_reason", KNOWN_FALSE_MATCH_PAIRS)
def test_serializer_refuses_known_false_pairs(supplier, external_id, target, _reason):
    row = desired(supplier_code=supplier, supplier_external_id=external_id, sterbrust_product_id=target)
    with pytest.raises(ContractValidationError, match="KNOWN_FALSE_MATCH_LEAK"):
        serialize_import_payload([row], evidence(), xml_contract())


def test_serializer_refuses_duplicate_targets_and_no_eligible_rows():
    with pytest.raises(ContractValidationError, match="DUPLICATE_TARGET"):
        serialize_import_payload([desired(), desired()], evidence(), xml_contract())
    with pytest.raises(ContractValidationError, match="NO_ELIGIBLE_ROW_INCLUDED"):
        serialize_import_payload([desired()], evidence(), xml_contract(),
                                 no_eligible_supplier_keys={("partner_st", "297")})


def _adapter(monkeypatch, allowed=("88585", "2")):
    monkeypatch.setenv("STERBRUST_WRITE_ENABLE", "YES")
    transport = CountingTransport()
    return SterbrustWriteAdapter(transport, evidence(), allowed, xml_contract()), transport


def test_write_adapter_sends_only_a_payload_matching_declared_targets(monkeypatch):
    adapter, transport = _adapter(monkeypatch)
    payload = serialize_import_payload([desired()], evidence(), xml_contract())
    assert adapter.execute(payload, ["88585"])["accepted"] is True and transport.calls == 1


@pytest.mark.parametrize("payload,targets,reason", [
    (None, ["88585", "2"], "differ"),                                     # declared more than the payload
    (b'<updates createNew="false"><product><existingId>99999</existingId></product></updates>', ["88585"], "allowlist"),
    (b'<updates createNew="true"><product><existingId>88585</existingId></product></updates>', ["88585"], "createNew"),
    (b'<updates><product><existingId>88585</existingId></product></updates>', ["88585"], "createNew"),
    (b'<updates createNew="false"><product><existingId>88585</existingId></product>'
     b'<product><existingId>88585</existingId></product></updates>', ["88585"], "Duplicate"),
    (b'<updates createNew="false"><create><existingId>88585</existingId></create></updates>', ["88585"], "Unexpected"),
    (b'<other createNew="false"/>', ["88585"], "root"),
    (b'<updates createNew="false">', ["88585"], "well-formed"),
])
def test_write_adapter_refuses_payload_mismatch(monkeypatch, payload, targets, reason):
    adapter, transport = _adapter(monkeypatch)
    body = payload or serialize_import_payload([desired()], evidence(), xml_contract())
    with pytest.raises(WriteRefused, match=reason):
        adapter.execute(body, targets)
    assert transport.calls == 0


def test_write_adapter_without_proven_xml_contract_refuses(monkeypatch):
    monkeypatch.setenv("STERBRUST_WRITE_ENABLE", "YES")
    transport = CountingTransport()
    adapter = SterbrustWriteAdapter(transport, evidence(), ["88585"])
    with pytest.raises(WriteRefused, match="MISSING_PROVEN_XML_CONTRACT"):
        adapter.execute(serialize_import_payload([desired()], evidence(), xml_contract()), ["88585"])
    assert transport.calls == 0


def test_xml_false_match_guards_come_from_the_contract():
    assert FALSE_MATCH_GUARDS == tuple((s, e, t) for s, e, t, _ in KNOWN_FALSE_MATCH_PAIRS)


# --- 9. Commercial parsing and title matching ------------------------------------------

def test_identity_match_keeps_cyrillic_letters():
    assert not _identity_matches("Энкор 405", "Корвет-405")
    assert _identity_matches("корвет 405", "Корвет-405")
    assert _identity_matches(["x", "BMS 230 DG"], "BMS-230DG")
    assert not _identity_matches("BMS-230DG-WP2", "BMS-230DG")
    assert not _identity_matches("", "")
    assert not _identity_matches("—", "-")


def _parse(html):
    from universal_supplier.commercial import _DocumentParser
    parser = _DocumentParser(); parser.feed(html); parser.close()
    return parser.root


@pytest.mark.parametrize("raw", ["руб.", "RUR", "₽", "rub", "RUB"])
def test_machine_currency_spellings_become_rub(raw):
    html = (f'<div itemscope><meta itemprop="price" content="12990"><meta itemprop="priceCurrency" content="{raw}">'
            f'<script type="application/ld+json">{{"@type":"Product","sku":"BMS-230DG",'
            f'"offers":{{"price":"12990","priceCurrency":"{raw}"}}}}</script></div>')
    root = _parse(html)
    assert {candidate.currency for candidate in _microdata(root)} == {"RUB"}
    assert {candidate.currency for candidate in _json_ld(root, "BMS-230DG")[0]} == {"RUB"}


def test_foreign_currency_is_kept_not_coerced():
    root = _parse('<div><meta itemprop="price" content="10"><meta itemprop="priceCurrency" content="usd"></div>')
    assert {candidate.currency for candidate in _microdata(root)} == {"USD"}


def test_ambiguous_three_digit_fraction_is_not_thousands():
    assert _parse_number("12 990.000") is None
    assert _parse_number("12990.000") is None
    assert _parse_number("12.990") == Decimal("12990")
    assert _parse_number("1.000.000") == Decimal("1000000")
    assert _parse_number("12 990,50") == Decimal("12990.50")


def test_title_represents_has_no_catastrophic_backtracking():
    model = "x" * 40
    title = "x" * 39 + " y" + "x" * 60
    started = time.perf_counter()
    assert title_represents(title, model) is False
    assert time.perf_counter() - started < 0.1
    assert title_represents("Станок 250×300 мм", "250x300")
    assert title_represents("Станок BMS 230 DG", "BMS-230DG")
