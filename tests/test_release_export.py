from copy import deepcopy
import json
from xml.etree import ElementTree as ET
import pytest
from universal_supplier.release_export import price_value, selection_view, validate, xml_bytes, tables

def existing(**changes):
    r=dict(source="test",external_id="1",classification="EXISTING_CONFIRMED",sterbrust_product_id="19870",
           model="TU2807V",execution="TU2807V",price_state="numeric_public",price="100.00",currency="RUB",
           availability="in_stock",observed_at="2026-10-02T12:00:00+00:00",evidence_ref="sha://abc")
    r.update(changes);return r

def new():
    p=dict(source="intervesp",external_id="full",new_group_id="NEWG-stable",new_candidate_id="NEWC-source",
           sterbrust_product_id=None,readiness=dict(identity_ready=True,full_ready=True,blockers=[]),
           optional_characteristics=[dict(role="OPTIONAL_CONFIGURATION",value=4500)],fields={})
    for key,value in dict(name="Saw",brand="MEBOR",model_execution="HTZ-800 RS",description="Own source",
        characteristics=[dict(role="pulley_width",value=80),dict(role="blade_width",value=100),
                        dict(role="weight_unspecified_basis",value=4100)],
        category=dict(id=4583,snapshot_active_verified=True),price=None,price_state="price_on_request").items():
        p["fields"][key]=dict(state="OBSERVED",value=value,provenance={})
    r=existing(source="intervesp",external_id="full",classification="READY_TO_CREATE_FULL",sterbrust_product_id=None,
               proposed_sterbrust_id=None,new_group_id="NEWG-stable",new_candidate_id="NEWC-source")
    return r,p

def test_unresolved_never_actionable_or_assigned():
    rows=[existing(classification=cls,external_id=cls) for cls in ("REVIEW","CONFLICT")]
    root=ET.fromstring(xml_bytes(rows,[],{}))
    assert len(root.findall("UNRESOLVED/Product"))==2
    assert all(n.get("actionable")=="false" and n.get("sterbrust_product_id") is None for n in root.findall("UNRESOLVED/Product"))

@pytest.mark.parametrize("state",["price_on_request","missing","ambiguous",None])
def test_request_and_unknown_price_never_zero(state):
    assert price_value(existing(price_state=state,price="0")) is None

def test_new_suffix_roles_options_and_null_survive():
    r,p=new(); original=deepcopy(p)
    root=ET.fromstring(xml_bytes([r],[p],{})); n=root.find("NEW/Product")
    assert n.get("sterbrust_product_id") is None
    assert json.loads(n.find("FullCard/model_execution").text)["value"]=="HTZ-800 RS"
    assert json.loads(n.find("FullCard/optional_configuration").text)==p["optional_characteristics"]
    assert p==original

@pytest.mark.parametrize("key",["name","brand","model_execution","category","description","characteristics"])
def test_unknown_mandatory_field_blocks(key):
    r,p=new();p["fields"][key]["state"]="UNKNOWN"
    with pytest.raises(ValueError):validate([r],[p])

def test_duplicate_identity_and_group_rejected():
    r,p=new()
    with pytest.raises(ValueError):validate([r,r],[p])
    with pytest.raises(ValueError):validate([r],[p,p])

def test_new_id_leak_rejected():
    r,p=new();r["proposed_sterbrust_id"]="999"
    with pytest.raises(ValueError):validate([r],[p])

def test_existing_invalid_canonical_id_rejected():
    with pytest.raises(ValueError):validate([existing(sterbrust_product_id="invented")],[])

def test_selection_excludes_review_unknown_and_request_price():
    rows=[existing(),existing(external_id="2",classification="REVIEW",price="1"),
          existing(external_id="3",availability=None,price="2"),
          existing(external_id="4",price_state="price_on_request",price=None)]
    assert selection_view(rows)=={("test","1")}

def test_deterministic_and_source_input_preserved():
    rows=[existing(),existing(external_id="2")];before=deepcopy(rows)
    assert xml_bytes(rows,[],{})==xml_bytes(list(reversed(rows)),[],{})
    assert rows==before
