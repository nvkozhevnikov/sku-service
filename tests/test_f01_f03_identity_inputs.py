"""F01/F03 integration regressions: synthetic SELECT loader, no real IO services."""
from collections import defaultdict
from dataclasses import replace
from itertools import permutations
import json
import socket

import pytest

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import normalize_model, normalize_model_strict
from universal_supplier.identity_service import (
    _load_cards, source_product, registry_product, classify_identity_card,
)
from universal_supplier.matching_service import (
    RegistryContext, _source, _candidate, _candidates, match_cards, load_registry_from_postgres,
)
from universal_supplier.models import ProductCard, PropertyValue, CategoryNode


@pytest.fixture(autouse=True)
def forbid_external_services(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("real network/DB forbidden in F01/F03 regression")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    # Runtime DB import is lazy; this suite invokes only pure functions/fake cursors.


def card(model="BD-7", props=(), supplier="optimum"):
    return ProductCard(
        supplier_code=supplier, external_id="S1", sku="", name=f"Станок токарный TEST {model}",
        requested_url="https://synthetic.invalid/1", final_url="https://synthetic.invalid/1",
        canonical_url="https://synthetic.invalid/1", http_status=200, redirect_urls=(),
        price=None, old_price=None, currency="RUB", availability_raw="",
        availability_normalized="unknown", quantity=None, description_text="", description_html="",
        brand="TEST", properties=tuple(PropertyValue(*x) for x in props),
        categories=(CategoryNode("Токарные станки", "", 0),),
        raw_data={"source_identity": {"raw_model": model}, "kind": "handle"},
    )


def registry(models=("BD-7",), props=None, reverse=False):
    products={}
    by_model=defaultdict(list)
    for number, model in enumerate(models, 101):
        key=str(number)
        products[key]={"sterbrust_product_id":key, "name":f"Станок токарный TEST {model}",
                       "brand":"TEST", "model":model, "article":"", "category":"Токарные станки",
                       "properties":props or {}, "supplier_identifiers":{}}
        by_model[normalize_model(model,"TEST")].append(key)
    if reverse:
        products=dict(reversed(list(products.items())))
        by_model={k:list(reversed(v)) for k,v in by_model.items()}
    return RegistryContext(products,{},by_model,{"test":list(products)},{})


class FakeConnection:
    """Routes actual loader SELECTs; rejects all other statements."""
    def __init__(self, product, registry_rows=None):
        self.product=product
        self.registry_rows=registry_rows
        self.result=[]
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=()):
        assert sql.lstrip().startswith("SELECT"),sql
        c=self.product
        if "FROM sterbrust_products" in sql:
            self.result=self.registry_rows or []
        elif "FROM sterbrust_product_supplier_identifiers" in sql:
            self.result=[]
        elif "FROM source_products sp" in sql:
            self.result=[(1,c.supplier_code,c.external_id,c.sku,c.name,c.requested_url,
                          c.canonical_url,c.brand,c.manufacturer,c.description_text,c.description_html,
                          c.raw_data,c.http_status,c.price,c.old_price,c.currency,c.availability_raw,
                          c.availability_normalized,c.quantity)]
        elif "FROM product_properties" in sql:
            self.result=[(1,x.name,x.value,x.unit,x.position) for x in c.properties]
        elif "FROM product_category_paths" in sql:
            self.result=[(1,x.name,x.url,x.position) for x in c.categories]
        else:raise AssertionError(sql)
    def fetchall(self):return self.result


def loaded(c):
    cards,ids=_load_cards(FakeConnection(c),c.supplier_code)
    assert ids=={(c.supplier_code,c.external_id):1}
    assert cards[0].raw_data==c.raw_data
    return cards[0]


@pytest.mark.parametrize("model,base",[("45/500S+","45/500S"),("50/650S+","50/650S"),("BF-1.5","BF 15")])
@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("constructor",[_source,lambda c: source_product(c.as_jsonable())],ids=["card","identity"])
def test_f01_both_candidates_loader_and_both_constructors(model,base,reverse,constructor):
    c=loaded(card(model));ctx=registry((base,model),reverse=reverse)
    for source in (constructor(c),):
        assert normalize_model_strict(source.model,source.brand)==normalize_model_strict(model)
        assert {x["sterbrust_product_id"] for x in _candidates(source,ctx)}=={"101","102"}
        assert not match_product(source,[_candidate(ctx.products["101"])]).auto_accepted
        result=match_product(source,[_candidate(x) for x in ctx.products.values()])
        assert not result.auto_accepted or result.candidate_key=="102"
    row=match_cards([c],ctx)[0]
    assert row["auto_accepted"]!="TRUE" or row["sterbrust_product_id"]=="102"
    # Materializer must also reject a stale, incorrect proposal.
    stale={**row,"sterbrust_product_id":"101","auto_accepted":"TRUE","match_method":"BRAND_MODEL"}
    assert classify_identity_card(c.as_jsonable(),stale,ctx.products)["auto_accepted"]=="FALSE"


@pytest.mark.parametrize("model,base",[("45/500S+","45/500S"),("50/650S+","50/650S"),("BF-1.5","BF 15")])
def test_f01_direct_raw_control_and_base_happy_path(model,base):
    ctx=registry((base,model))
    raw=Product("S",f"Станок токарный TEST {model}","TEST",model,category="Токарные станки")
    assert not match_product(raw,[_candidate(ctx.products["101"])]).auto_accepted
    assert match_product(raw,[_candidate(x) for x in ctx.products.values()]).candidate_key=="102"
    assert match_cards([card(base)],registry((base,)))[0]["auto_accepted"]=="TRUE"


BAD_GROUPS=[
    (("Напряжение","220 В"),("Напряжение","380 В")),
    (("Напряжение","220","В"),("Напряжение","380","В")),
    (("Напряжение","220"),("Напряжение","380")),
    (("Напряжение","220 В"),("Voltage","380 V")),
    (("Напряжение","220 В"),("Напряжение","220 кВт")),
    (("Мощность двигателя","1.5","кВт"),("Мощность двигателя","3","кВт")),
]


@pytest.mark.parametrize("props",BAD_GROUPS)
@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("constructor",[_source,lambda c: source_product(c.as_jsonable())],ids=["card","identity"])
def test_f03_ambiguous_source_blocks_auto_in_both_paths(props,reverse,constructor):
    c=loaded(card(props=props[::-1] if reverse else props))
    ctx=registry(props={"Напряжение":"220 В","Мощность двигателя, кВт":"1.5"})
    for source in (constructor(c),):
        result=match_product(source,[_candidate(ctx.products["101"])])
        assert not result.auto_accepted
    row=match_cards([c],ctx)[0]
    assert row["auto_accepted"]=="FALSE"
    stale={**row,"sterbrust_product_id":"101","auto_accepted":"TRUE","match_method":"BRAND_MODEL"}
    assert classify_identity_card(c.as_jsonable(),stale,ctx.products)["auto_accepted"]=="FALSE"


@pytest.mark.parametrize("value",["220/380 В","220 / 380 V"])
@pytest.mark.parametrize("target",["220 В","380 В","220/380 В"])
def test_f03_voltage_set_does_not_choose_one_execution(value,target):
    c=card(props=(("Напряжение",value),))
    assert match_cards([c],registry(props={"Напряжение":target}))[0]["auto_accepted"]=="FALSE"


@pytest.mark.parametrize("props",[
    (("Напряжение","220","В"),("Напряжение","220 В")),
    (("Мощность двигателя","1.5","кВт"),("Мощность двигателя","1500 Вт")),
    (("Цвет","красный"),("Цвет","синий")),
])
def test_f03_equal_or_nonidentity_duplicates_do_not_blanket_block(props):
    outcomes=[]
    for perm in permutations(props):
        c=card(props=perm)
        ctx=registry(props={"Напряжение":"220 В","Мощность двигателя, кВт":"1.5"})
        row=match_cards([c],ctx)[0];outcomes.append(row)
        assert row["auto_accepted"]=="TRUE"
    assert outcomes[0]==outcomes[-1]


def test_f03_target_loader_retains_all_values_and_units():
    rows=[("101","Станок токарный TEST BD-7","TEST","BD-7","","Токарные станки",
           {"items":[{"property_name":"Напряжение","value_flat":"380","unit":"В"},
                     {"property_name":"Напряжение","value_flat":"220","unit":"В"}]})]
    ctx=load_registry_from_postgres(FakeConnection(card(),rows))
    assert match_cards([card(props=(("Напряжение","220 В"),))],ctx)[0]["auto_accepted"]=="FALSE"
    target=registry_product({"sterbrust_product_id":"101","name":rows[0][1],"brand_raw":"TEST",
                             "model_raw":"BD-7","category_path":"Токарные станки","properties":rows[0][-1]})
    assert not match_product(_source(card(props=(("Напряжение","220 В"),))),[target]).auto_accepted


def test_f03_existing_list_target_and_unknown_opposite_stay_safe():
    source=_source(card(props=(("Напряжение","220 В"),)))
    ctx=registry(props={"Напряжение":["220 В","380 В"]})
    assert not match_product(source,[_candidate(ctx.products["101"])]).auto_accepted
    c=card(props=(("Напряжение","220 В"),("Напряжение","380 В")))
    assert match_cards([c],registry())[0]["auto_accepted"]=="FALSE"


def test_f03_representation_is_order_independent_and_lossless():
    props=(("Напряжение","220","В"),("Напряжение","380","В"))
    left=_source(card(props=props));right=_source(card(props=props[::-1]))
    assert left.properties==right.properties
    assert "220" in str(left.properties) and "380" in str(left.properties)
    assert "В" in str(left.properties)


@pytest.mark.parametrize("model",["45/500S+","BF-1.5"])
def test_manual_canonical_id_is_not_replaced(model):
    c=card(model);ctx=registry(("BD-7",model))
    link={"S1":{"sterbrust_product_id":"101","manual_or_human_confirmed":True,
                "origin_match_method":"MANUAL_CONFIRMED"}}
    result=match_cards([c],ctx,link)[0]
    assert result["sterbrust_product_id"]=="101"
    assert result["match_method"]=="EXISTING_LINK_MANUAL"
    assert result["auto_accepted"]=="FALSE"
    assert result==match_cards([c],ctx,link)[0]
    assert classify_identity_card(c.as_jsonable(),result,ctx.products)["decision"]=="EXACT_EXISTING"


@pytest.mark.parametrize("name",["Ручка для станка TEST BD-7+","Контроллер для TEST BD-7+"])
def test_reference_model_never_becomes_own_machine_model(name):
    c=replace(card(),name=name)
    for s in (_source(c),source_product(c.as_jsonable())):
        assert not s.model
        assert not match_product(s,[_candidate(registry(("BD-7+",)).products["101"])]).auto_accepted


def test_f02_raw_kind_is_not_silently_added_by_this_fix():
    assert _source(card()).product_kind==""
    assert source_product(card().as_jsonable()).product_kind==""

@pytest.mark.parametrize("constructor",[_source,lambda c: source_product(c.as_jsonable())],ids=["card","identity"])
def test_decimal_comma_is_not_a_truncated_model(constructor):
    c=card("BF-1,5");s=constructor(c);ctx=registry(("BF 15","BF-1.5"))
    assert normalize_model_strict(s.model,s.brand)=="bf1.5"
    result=match_product(s,[_candidate(x) for x in ctx.products.values()])
    assert not result.auto_accepted or result.candidate_key=="102"


@pytest.mark.parametrize("model,base",[("6K+2","6K+"),("45S+PRO","45S"),("ATC1+C","ATC1+")])
def test_embedded_plus_execution_is_not_truncated(model,base):
    for s in (_source(card(model)),source_product(card(model).as_jsonable())):
        assert normalize_model_strict(s.model,s.brand)==normalize_model_strict(model)
        assert not match_product(s,[_candidate(registry((base,)).products["101"])]).auto_accepted


@pytest.mark.parametrize("raw,key,newc,newg",[
    ("45/500S+","45500s","NEWC-79A197237A0522EFF4FE498E","NEWG-88E3A7F87898BB2F581E5C5B"),
    ("50/650S+","50650s","NEWC-B8429B43090E0DA979989D6F","NEWG-18DD9202C0C1D89BCB473B3E"),
    ("BF-1.5","bf15","NEWC-8A103601DA8172E8334ACDCA","NEWG-2C805717446BFC68064BF9B7"),
])
def test_legacy_keys_and_technical_id_golden_vectors(raw,key,newc,newg):
    from universal_supplier.proposal_export import new_candidate_id
    from universal_supplier.new_group_proposals import _group_id
    assert normalize_model(raw)==key
    for c in (_source(card(raw)),source_product(card(raw).as_jsonable())):
        assert normalize_model(c.model,c.brand)==key
        assert new_candidate_id(c.supplier_code,c.key,normalize_model(c.model,c.brand))==newc
        assert _group_id(c)==newg


@pytest.mark.parametrize("props",[
    {"power":("1500 W","3000 W")},
    {"voltage":("220 V","380 V")},
    {"Мощность двигателя":("1.5 кВт","3 кВт")},
])
def test_critical_multivalue_not_silenced_by_missing_opposite_property(props):
    s=replace(_source(card()),properties=props)
    assert not match_product(s,[_candidate(registry().products["101"])]).auto_accepted


@pytest.mark.parametrize("left,right",[
    ("LX20 PRO","LX20 NEW"),("PP-13D NEW","PP-13D"),("TU2304V","TU2304"),
    ("A1 NC","A1 CNC"),
])
def test_execution_guards_through_both_constructors(left,right):
    for s in (_source(card(left)),source_product(card(left).as_jsonable())):
        assert not match_product(s,[_candidate(registry((right,)).products["101"])]).auto_accepted


def test_empty_values_do_not_erase_observed_value():
    c=card(props=(("Напряжение","220 В"),("Напряжение",""),("Напряжение"," ")))
    assert match_cards([c],registry(props={"Напряжение":"220 В"}))[0]["auto_accepted"]=="TRUE"


@pytest.mark.parametrize("props",[
    (("Напряжение","220 V","В"),("Напряжение","220 В","V")),
    (("Мощность двигателя","1.5 kW","кВт"),("Мощность двигателя","1500 W","Вт")),
    (("Мощность двигателя","1.5kW"),("Мощность двигателя","1500W")),
])
def test_equivalent_unit_column_does_not_create_artificial_ambiguity(props):
    ctx=registry(props={"Напряжение":"220 В","Мощность двигателя, кВт":"1.5"})
    for perm in permutations(props):
        assert match_cards([card(props=perm)],ctx)[0]["auto_accepted"]=="TRUE"


def test_operator_rejection_and_postponed_contract_unchanged():
    from universal_supplier.postgres import operator_decision_guard
    row=match_cards([card()],registry())[0]
    accepted=operator_decision_guard(row,{}, {}, {}, "",rejected=False,final_decision_class="POSTPONED")
    assert accepted is None  # deferred review does not itself revoke authority
    guarded=operator_decision_guard(row,{}, {}, {}, "",rejected=True,final_decision_class=None)
    assert guarded[0]["auto_accepted"] is False
    assert guarded[0]["status"]=="REVIEW"


@pytest.mark.parametrize("article",["10-100","10100","101-00","00101"])
def test_supplier_namespace_never_changes_with_input_view(article):
    c=replace(card(),sku=article,supplier_code="optimum")
    target=replace(_candidate(registry(("BD-8",)).products["101"]),
                   supplier_identifiers={"partner_st":(article,)})
    for s in (_source(c),source_product(c.as_jsonable())):
        assert s.supplier_code=="optimum"
        assert not match_product(s,[target]).auto_accepted
