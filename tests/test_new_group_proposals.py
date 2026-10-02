from copy import deepcopy
from itertools import permutations

import pytest

from universal_supplier.new_group_proposals import propose_new_groups, singleton_proposals
from universal_supplier.proposal_export import new_candidate_id
from sterbrust_matching.normalization import normalize_model


def candidate(source="beka_mak", external_id="19240", model="BMS-230DG", **updates):
    row = {
        "source": source, "external_id": external_id,
        "new_candidate_id": new_candidate_id(source, external_id, normalize_model(model)),
        "classification": "NEW_CANDIDATE", "brand": "Beka-Mak", "model": model,
        "execution": model, "name": f"Ленточнопильный станок Beka-Mak {model}",
        "product_kind": "machine", "model_role": "OWN_MODEL",
        "source_url": f"https://fixture.invalid/{source}/{external_id}",
        "evidence_ref": f"fixture://sanitized/{source}/{external_id}",
        "properties": {"diameter": "230 mm", "voltage": "380 V"},
    }
    row.update(updates)
    return row


def trio():
    return [candidate(), candidate("beka_mak_tr", "BMS-230DG", brand="BEKAMAK"),
            candidate("intervesp", "8992", model="BMS 230 DG", brand="Beka Mak")]

def test_five_namespace_scope_is_explicit_and_identity_guards_unchanged():
    rows=trio()+[candidate('partner_st','p'),candidate('optimum','o')]
    scope={'partner_st','optimum','intervesp','beka_mak','beka_mak_tr'}
    result=propose_new_groups(rows,allowed_sources=scope)
    assert len(result['groups'])==1
    assert len(result['groups'][0]['source_records'])==5
    default=propose_new_groups(rows)
    # Default three-source scope quarantines the related cohort, rather than
    # silently ignoring an unsupported fourth/fifth source (existing guard).
    assert {r['source'] for r in default['review']}==scope
    assert not default['groups']
    with pytest.raises(ValueError):propose_new_groups(rows,allowed_sources=scope|{'production'})


def test_strict_singleton_has_same_technical_group_id_without_equivalence_claim():
    single = singleton_proposals(propose_new_groups(trio()[:1]))[0]
    multiple = propose_new_groups(trio())["groups"][0]
    assert single["new_group_id"] == multiple["new_group_id"]
    assert single["cross_source_equivalence_claimed"] is False
    assert single["equivalence_evidence"] == []
    assert single["sterbrust_product_id"] is None


def test_incomplete_singleton_cannot_bypass_grouping_review():
    result = propose_new_groups([candidate(brand="")])
    assert not singleton_proposals(result)
    with pytest.raises(ValueError):
        singleton_proposals({"ungrouped": [{**candidate(brand=""), "grouping_status": "SINGLE_SOURCE_NEW"}]})


def test_typed_anchor_without_scoped_safe_provenance_cannot_group():
    rows = [candidate(properties={'typed_bandsaw_blade_length':'3660 mm'}),
            candidate('intervesp','other',properties={'typed_bandsaw_blade_length':'3660 mm'})]
    assert_review(rows, 'typed_anchor_provenance_missing_or_scope_mismatch')


def test_empty_serialized_diagnostics_do_not_create_an_artificial_review():
    rows=trio()
    for row in rows: row.update(match_conflicts='{}',semantic_conflicts='[]')
    assert len(propose_new_groups(rows)['groups'])==1
    rows[0]['match_conflicts']='{"execution":"mismatch"}'
    assert_review(rows,'upstream_semantic_contradiction')


def assert_review(rows, reason):
    result = propose_new_groups(rows)
    assert result["groups"] == []
    assert result["ungrouped"] == []
    assert result["review"]
    assert all(row["new_group_id"] is None for row in result["review"])
    assert any(reason in row["grouping_reasons"] for row in result["review"])
    return result


def test_three_sources_one_group_keeps_individual_candidate_ids():
    rows = trio()
    result = propose_new_groups(rows)
    assert not result["review"] and not result["ungrouped"]
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["new_group_id"].startswith("NEWG-")
    assert group["sterbrust_product_id"] is None
    assert len(group["source_records"]) == 3
    assert {row["new_candidate_id"] for row in group["source_records"]} == {
        row["new_candidate_id"] for row in rows}
    assert len(group["equivalence_evidence"]) == 3


def test_group_is_deterministic_order_independent_and_stable_when_third_source_added():
    rows = trio()
    first = propose_new_groups(rows)
    for ordering in permutations(rows):
        assert propose_new_groups(ordering) == first
    assert propose_new_groups(rows[:2])["groups"][0]["new_group_id"] == first["groups"][0]["new_group_id"]


@pytest.mark.parametrize("left,right", [
    ("TU2304V", "TU2304"), ("LX20 Pro", "LX20 NEW"),
    ("BMSY-440DGH WP2", "BMSY-440DGH"), ("BMSY-440DGH WP1", "BMSY-440DGH WP2"),
    ("BMSY-360DGH ECO", "BMSY-360DGH"),
])
def test_full_model_execution_variants_remain_review(left, right):
    assert_review([candidate(model=left), candidate("intervesp", "other", model=right)],
                  "full_model_or_execution_mismatch")


def test_hcv125_jaws_cannot_group_with_hcv125_vise():
    jaws = candidate(model="HCV125", name="HCV125 jaws", brand="Optimum", product_kind="jaw")
    vise = candidate("intervesp", "vise", model="HCV125", name="Тиски Optimum HCV125",
                     brand="Optimum", product_kind="vise")
    assert_review([jaws, vise], "product_context_mismatch")


def test_explicit_parent_reference_remains_review_even_with_matching_accessory_type():
    left = candidate(model="HCV125", name="Губки для тисков HCV125", product_kind="jaw",
                     model_role="REFERENCE_MODEL")
    right = {**left, "source": "intervesp", "external_id": "jaws2", "new_candidate_id": "NEWC-JAWS2"}
    assert_review([left, right], "own_model_not_proven")


@pytest.mark.parametrize("field,value,reason", [
    ("brand", "", "insufficient_brand_or_full_model"),
    ("model", "", "insufficient_brand_or_full_model"),
    ("model_role", "", "own_model_not_proven"),
    ("evidence_ref", "", "missing_source_evidence"),
    ("source_url", "", "missing_source_evidence"),
    ("product_kind", "unknown", "unknown_product_context"),
    ("new_candidate_id", "", "missing_new_candidate_id"),
])
def test_single_source_insufficient_evidence_remains_review(field, value, reason):
    row = candidate()
    row[field] = value
    assert_review([row], reason)


def test_brand_model_alone_or_generic_electrical_ratings_are_insufficient():
    rows = [candidate(properties={"voltage": "380 V", "power": "1.5 kW"}),
            candidate("intervesp", "second", properties={"voltage": "380 V", "power": "1.5 kW"})]
    assert_review(rows, "independent_equivalence_evidence_missing")


def test_ambiguous_third_source_cannot_be_dropped_to_confirm_partial_group():
    rows = trio()
    rows[-1]["brand"] = ""
    result = assert_review(rows, "insufficient_brand_or_full_model")
    assert len(result["review"]) == 3


def test_semantic_property_contradiction_quarantines_all_three_members():
    rows = trio()
    rows[-1]["properties"] = {"diameter": "325 mm", "voltage": "380 V"}
    result = assert_review(rows, "semantic_or_property_contradiction")
    assert len(result["review"]) == 3


def test_upstream_conflict_and_title_suffix_omission_are_not_ignored():
    assert_review([candidate(semantic_conflicts=["upper_clamp_configuration_differs"])],
                  "upstream_semantic_contradiction")
    assert_review([candidate(model="TU2304", name="Токарный станок TU2304V", product_kind="lathe")],
                  "title_model_execution_contradiction")


def test_explicit_execution_cannot_be_omitted_from_full_model():
    assert_review([candidate(execution="WP2")], "full_model_execution_contradiction")


def test_no_transitive_bridge_when_outer_members_have_no_shared_equivalence_evidence():
    rows = trio()
    rows[0]["properties"] = {"diameter": "230 mm"}
    rows[1]["properties"] = {"diameter": "230 mm", "length": "300 mm"}
    rows[2]["properties"] = {"length": "300 mm"}
    assert_review(rows, "shared_equivalence_evidence_missing")


def test_unit_variation_uses_existing_typed_property_rules():
    rows = trio()
    rows[-1]["properties"] = {"diameter": "23 cm", "voltage": "380 В"}
    result = propose_new_groups(rows)
    assert len(result["groups"]) == 1
    assert not result["review"]


def test_independent_manufacturer_article_can_supply_positive_equivalence():
    rows = [candidate(properties={}, manufacturer_article="FACTORY-4711",
                      manufacturer_article_evidence_ref="fixture://factory/article"),
            candidate("intervesp", "8992", properties={}, manufacturer_article="FACTORY-4711",
                      manufacturer_article_evidence_ref="fixture://supplier/printed-factory-article")]
    result = propose_new_groups(rows)
    assert len(result["groups"]) == 1
    assert result["groups"][0]["equivalence_evidence"][0]["manufacturer_article_equal"]


def test_model_repeated_as_manufacturer_article_is_not_independent_evidence():
    assert_review([candidate(properties={}, manufacturer_article="BMS-230DG",
                             manufacturer_article_evidence_ref="fixture://model-only")],
                  "independent_equivalence_evidence_missing")


def test_identical_replay_has_one_member_per_source_and_no_input_mutation():
    rows = trio()
    before = deepcopy(rows)
    result = propose_new_groups(rows + [deepcopy(rows[0])])
    assert rows == before
    members = [member for group in result["groups"] for member in group["source_records"]]
    assert len(members) == len({(row["source"], row["external_id"]) for row in members}) == 3


def test_conflicting_copies_of_same_source_product_never_enter_a_group():
    rows = [candidate(), candidate(model="TU2304V")]
    result = assert_review(rows, "duplicate_source_identity_conflict")
    assert len(result["review"]) == 2


def test_same_candidate_id_cannot_be_reused_by_different_source_records():
    rows = trio()
    rows[1]["new_candidate_id"] = rows[0]["new_candidate_id"]
    assert_review(rows, "new_candidate_id_collision")


def test_multiple_distinct_products_from_one_supplier_are_not_collapsed():
    assert_review([candidate(), candidate(external_id="another")], "multiple_products_from_one_supplier")


def test_strong_single_source_record_keeps_candidate_id_without_cross_source_group():
    row = candidate()
    result = propose_new_groups([row])
    assert result["groups"] == [] and result["review"] == []
    assert result["ungrouped"][0]["new_candidate_id"] == row["new_candidate_id"]
    assert result["ungrouped"][0]["new_group_id"] is None


def test_group_id_does_not_depend_on_price_and_canonical_ids_are_never_created():
    rows = trio()
    first = propose_new_groups(rows)
    rows[0]["price"] = "123456"
    assert propose_new_groups(rows)["groups"][0]["new_group_id"] == first["groups"][0]["new_group_id"]
    assert_review([candidate(sterbrust_product_id="17128")], "existing_canonical_candidate_present")


def test_review_and_conflict_are_never_promoted_to_new():
    for classification in ("REVIEW", "CONFLICT"):
        result = assert_review([candidate(classification=classification)], "not_new_candidate")
        assert result["review"][0]["classification"] == classification


def test_unknown_numeric_property_units_do_not_prove_equivalence():
    assert_review([candidate(properties={"diameter": "230 bananas"})],
                  "independent_equivalence_evidence_missing")


def test_conflicting_third_source_replay_cannot_leave_a_confirmed_partial_group():
    rows = trio()
    changed = {**rows[-1], "execution": "WP2"}
    result = assert_review(rows + [changed], "duplicate_source_identity_conflict")
    assert len(result["review"]) == 4


@pytest.mark.parametrize("field,value", [("properties", []), ("model", "")])
def test_incomplete_third_source_keeps_whole_related_cohort_in_review(field, value):
    rows = trio()
    rows[-1][field] = value
    result = propose_new_groups(rows)
    assert result["groups"] == []
    assert len(result["review"]) == 3


def test_different_title_model_cannot_hide_behind_same_explicit_model():
    assert_review([candidate(name="Ленточнопильный станок BMS-325DG")],
                  "title_model_execution_contradiction")


def test_canonical_category_properties_are_compared_with_typed_units():
    rows = [candidate(model="TU2304V", name="Токарный станок TU2304V", product_kind="lathe",
                      properties={"distance_between_centers": "2500 mm"}),
            candidate("intervesp", "lathe", model="TU 2304 V", name="Токарный станок TU 2304 V",
                      product_kind="lathe", properties={"distance_between_centers": "2.5 m"})]
    result = propose_new_groups(rows)
    assert len(result["groups"]) == 1
    assert not result["review"]


def test_incompatible_brand_is_not_aliased_by_similarity():
    assert_review([candidate(), candidate("intervesp", "second", brand="Beka-Mak-like")],
                  "brand_mismatch_or_missing")


@pytest.mark.parametrize("article,evidence", [("19240", "fixture://internal-id"),
                                            ("FACTORY-4711", "")])
def test_supplier_id_or_unproven_article_cannot_prove_equivalence(article, evidence):
    assert_review([candidate(properties={}, manufacturer_article=article,
                             manufacturer_article_evidence_ref=evidence)],
                  "independent_equivalence_evidence_missing")
