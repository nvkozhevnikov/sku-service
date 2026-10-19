"""Identity comparisons outside the core matcher use the strict model/article keys.

Retrieval indexes, persisted keys and technical IDs stay on the legacy key; only
"is this the same product?" decisions move to the strict key.
"""
from __future__ import annotations

from sterbrust_matching.matching import Product
from sterbrust_matching.normalization import normalize_model
from universal_supplier import new_group_proposals
from universal_supplier.kami_overlap import full_key
from universal_supplier.new_group_proposals import _group_id, _pair_evidence
from universal_supplier.offline_review_resolution import resolve_existing


def test_kami_overlap_full_key_keeps_decimal_and_plus():
    assert full_key({"brand": "Proma", "model": "BF-1.5"}) != full_key({"brand": "Proma", "model": "BF 15"})
    assert full_key({"brand": "JET", "model": "JWL-1015+"}) != full_key({"brand": "JET", "model": "JWL-1015"})
    assert full_key({"brand": "JET", "model": "JWL-1015"}) == full_key({"brand": "JET", "model": "JWL 1015"})


def test_offline_resolution_blocks_decimal_execution_difference():
    row = {"brand": "Proma", "model": "BF-1.5", "name": "Станок Proma BF-1.5"}
    target = {"brand": "Proma", "model": "BF 15", "name": "Станок Proma BF 15", "sterbrust_product_id": "1"}
    assert "FULL_MODEL_EXECUTION_MISMATCH" in resolve_existing(row, target)["blocking_reasons"]
    same = {**target, "model": "BF 1,5", "name": "Станок Proma BF 1,5"}
    assert "FULL_MODEL_EXECUTION_MISMATCH" not in resolve_existing(row, same)["blocking_reasons"]


def test_new_group_pair_article_equality_uses_article_compatibility():
    def product(article):
        return Product(key=article, name="Станок JET VS-100", brand="jet", model="VS-100", manufacturer_article=article)
    _, formatted = _pair_evidence(product("303 4252"), product("3034252"))
    _, different = _pair_evidence(product("10-100"), product("101-00"))
    assert formatted["manufacturer_article_equal"] is True
    assert different["manufacturer_article_equal"] is False


def test_new_group_id_stays_on_legacy_key():
    product = Product(key="x", name="Станок JET JWL-1015+", brand="jet", model="JWL-1015+", product_kind="lathe")
    legacy = Product(key="y", name="Станок JET JWL 1015", brand="jet", model="JWL 1015", product_kind="lathe")
    # Technical group IDs must not change for already published proposals.
    assert normalize_model(product.model, product.brand) == normalize_model(legacy.model, legacy.brand)
    assert _group_id(product) == _group_id(legacy)


def test_new_group_cohort_with_decimal_variants_goes_to_review(monkeypatch):
    monkeypatch.setattr(new_group_proposals, "_individual_reasons", lambda row, product, allowed_sources=None: [])
    monkeypatch.setattr(new_group_proposals, "_pair_evidence", lambda left, right: ([], {}))
    rows = [{"source": source, "external_id": source + "-1", "brand": "Proma", "model": model, "name": "Станок Proma " + model,
             "product_kind": "machine", "new_candidate_id": "N-" + source, "classification": "NEW_CANDIDATE"}
            for source, model in (("beka_mak", "BF-1.5"), ("intervesp", "BF 15"))]
    result = new_group_proposals.propose_new_groups(rows)
    assert result["groups"] == []
    assert all("full_model_or_execution_mismatch" in row["grouping_reasons"] for row in result["review"])
