from __future__ import annotations

from collections import defaultdict

import pytest

import sterbrust_matching.matching as matching_module
import sterbrust_matching.normalization as normalization_module
import universal_supplier.matching_service as matching_service_module
from scripts import evaluate_strict_identity_keys as script
from sterbrust_matching.normalization import normalize_brand, normalize_model
from universal_supplier.matching_service import RegistryContext, _optimum_title_articles
from universal_supplier.models import ProductCard
from universal_supplier.strict_key_evaluation import compare, key_patterns, legacy_identity_keys, summarize, transition


def registry(rows):
    products, by_model, by_brand, titles = {}, defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        row = {"article": "", "category": "", "properties": {}, **row,
               "supplier_identifiers": defaultdict(list, row.get("ids", {}))}
        key = row["sterbrust_product_id"]; products[key] = row
        by_model[normalize_model(row["model"], row["brand"])].append(key)
        by_brand[normalize_brand(row["brand"])].append(key)
        for article in _optimum_title_articles(row["name"]):
            titles[("optimum", article)].append(key)
    return RegistryContext(products, {}, by_model, by_brand, titles)


def card(code, external_id, sku, name, brand):
    return ProductCard(code, external_id, sku, name, "u", "u", "u", 200, (), None, None, "RUB", "", "unknown", None, "", "", brand=brand)


REGISTRY = registry([
    {"sterbrust_product_id": "1", "name": "Сверлильный станок Optimum 3034252 OPTIdrill DH35V", "brand": "Optimum", "model": "DH35V"},
    {"sterbrust_product_id": "2", "name": "Ленточнопильный станок JET JWBS-14", "brand": "JET", "model": "JWBS-14", "ids": {"partner_st": ["710-1150"]}},
    {"sterbrust_product_id": "3", "name": "Станок Proma BF-1.5", "brand": "Proma", "model": "BF-1.5"},
    {"sterbrust_product_id": "4", "name": "Станок JET JWL-1015+", "brand": "JET", "model": "JWL-1015+"},
    {"sterbrust_product_id": "5", "name": "Тиски JET VS-100", "brand": "JET", "model": "VS-100", "ids": {"partner_st": ["101-00"]}},
])
CARDS = [
    card("optimum", "a", "303 4252", "Сверлильный станок OPTIdrill DH35V", "Optimum"),
    card("partner_st", "c", "7101150", "Ленточнопильный станок JET JWBS-14", "JET"),
    card("partner_st", "e", "x1", "Станок Proma BF 15", "Proma"),
    card("partner_st", "f", "x2", "Станок JET JWL-1015", "JET"),
    card("partner_st", "g", "10-100", "Тиски JET VS-100", "JET"),
]


def test_ab_comparison_isolates_strict_key_effects():
    rows = {row["source_external_id"]: row for row in compare(CARDS, REGISTRY, {})}
    # Formatting-only article differences keep their decision.
    assert rows["a"]["transition"] == "UNCHANGED" and rows["a"]["strict_auto_accepted"]
    assert rows["c"]["transition"] == "UNCHANGED" and rows["c"]["strict_method"] == "SUPPLIER_ARTICLE_EXACT"
    # Decimal point and semantic plus stop auto-acceptance.
    assert rows["e"]["transition"] == "AUTO_TO_NOT_AUTO" and rows["e"]["patterns"] == "MODEL_DECIMAL_POINT"
    assert rows["f"]["transition"] == "AUTO_TO_NOT_AUTO" and rows["f"]["patterns"] == "MODEL_PLUS"
    # 10-100 is no longer the supplier article 101-00; the model alone still decides.
    assert rows["g"]["legacy_method"] == "SUPPLIER_ARTICLE_EXACT" and rows["g"]["strict_method"] == "BRAND_MODEL"
    assert rows["g"]["patterns"] == "ARTICLE_DIGIT_GROUPS"
    summary = summarize(list(rows.values()))
    assert summary["transitions"] == {"AUTO_TO_NOT_AUTO": 2, "STATUS_OR_METHOD_CHANGED": 1, "UNCHANGED": 2}
    assert summary["legacy_auto_accepted"] == 5 and summary["strict_auto_accepted"] == 3


def test_legacy_patch_is_restored_even_on_error():
    before = [(module, name, getattr(module, name)) for module, name in (
        (normalization_module, "normalize_model_strict"), (matching_module, "normalize_model_strict"),
        (matching_module, "articles_compatible"), (matching_service_module, "article_lookup_key"))]
    with pytest.raises(RuntimeError), legacy_identity_keys():
        assert matching_module.articles_compatible("10-100", "101-00")
        raise RuntimeError("boom")
    assert all(getattr(module, name) is value for module, name, value in before)
    assert not matching_module.articles_compatible("10-100", "101-00")


def test_transition_classes():
    base = {"status": "HIGH_CONFIDENCE_MATCH", "method": "BRAND_MODEL", "candidate": "1", "auto_accepted": True}
    assert transition(base, dict(base)) == "UNCHANGED"
    assert transition(base, {**base, "auto_accepted": False, "status": "REVIEW"}) == "AUTO_TO_NOT_AUTO"
    assert transition({**base, "auto_accepted": False}, base) == "NOT_AUTO_TO_AUTO"
    assert transition({**base, "auto_accepted": False}, {**base, "auto_accepted": False, "candidate": "2"}) == "CANDIDATE_CHANGED"


def test_key_patterns():
    assert key_patterns(["BF-1.5", "JWL+"], ["303 4252"]) == ["ARTICLE_DIGIT_GROUPS", "MODEL_DECIMAL_POINT", "MODEL_PLUS"]
    assert key_patterns(["DH35V"], ["3034252"]) == []


def test_script_refuses_existing_output_directory(tmp_path):
    with pytest.raises(SystemExit):
        script.main(["--out", str(tmp_path)])


def test_script_refuses_connection_that_is_not_read_only(monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): self.sql = sql
        def fetchone(self): return ("off",)

    class Connection:
        def cursor(self): return Cursor()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: Connection())
    with pytest.raises(RuntimeError, match="not read-only"):
        script.read_only_connection(type("Config", (), {"kwargs": lambda self: {}})())
