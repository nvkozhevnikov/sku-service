from dataclasses import replace
from decimal import Decimal

import pytest

from universal_supplier.adapters.ivstankomaster import BASE, CODE, parse_detail
from universal_supplier.commercial import PriceState
from universal_supplier.control_plane.adapter_registry import default_adapter_registry
from universal_supplier.control_plane.source_discovery import decode_node, root_urls
from universal_supplier.control_plane.source_jobs import FROZEN_LIVE11_SOURCES, validate_url
from scripts.build_ivstankomaster_export import links_for

URL = BASE + "/item/test"


def card(prices, stock="", title="Станок СЗТП 600Т"):
    return f"""
    <ol class="breadcrumb"><li aria-current="page">{title}</li></ol>
    <div class="item-additionally"><button class="add_item" data-id="19"
         data-title="{title}" data-article="СЗТП 600Т" data-retail="230000"
         data-allcount="{stock}">Добавить</button></div>
    <div id="item-description">{prices}</div>
    <button class="add_item" data-id="20" data-retail="10">Рекомендация</button>
    """


@pytest.mark.parametrize(("html", "expected", "basis"), [
    ("<p>230 000 руб (без НДС)/280 600 руб (с НДС)</p>", "280600", "explicit_vat"),
    ("<p>230 000 руб (без НДС)/280 600</p>", "280600", "ordered_pair_supplier_terms"),
    ("<p>200 000 руб (без НДС)/280 600</p>", None, None),
    ("<p>230 000 руб (без НДС)</p>", None, None),
    ("<p>Цена 280 600 (с НДС)</p><p>300 000 (с НДС)</p>", None, None),
])
def test_vat_from_own_card_without_fabrication(html, expected, basis):
    result = parse_detail(card(html), source_url=URL)
    assert result.price.current_price == (Decimal(expected) if expected else None)
    assert result.price.state is (PriceState.NUMERIC_PUBLIC if expected else PriceState.AMBIGUOUS)
    assert result.enrichment_evidence["vat_basis"] == basis
    assert result.site_internal_id == "19"
    assert result.manufacturer_article is None
    assert result.availability is None


def test_scope_identity_and_stock():
    result = parse_detail(card("<p>280600 (с НДС)</p>", stock="3"), source_url=URL)
    assert result.source_quantity == 3 and result.availability == "InStock"
    with pytest.raises(ValueError, match="идентичности"):
        parse_detail(card("").replace('data-title="Станок','data-title="Тумба'), source_url=URL)
    for candidate in (BASE+"/item/test?alt=1", BASE+"/category", "https://else.invalid/item/test"):
        with pytest.raises(ValueError):
            validate_url(CODE,candidate)


def test_discovery_and_legacy_scope_are_separate():
    seed, = root_urls(CODE)
    nodes, items = decode_node(CODE,seed,b'<a href="/category/section">a</a><a href="/item/card">b</a><a href="https://bad.invalid/item/c">c</a>')
    assert nodes==[BASE+"/category/section"] and [item["url"] for item in items]==[BASE+"/item/card"]
    assert CODE not in FROZEN_LIVE11_SOURCES and len(FROZEN_LIVE11_SOURCES)==11
    assert default_adapter_registry().supports(CODE,"full")


def test_candidate_exact_model_beats_compatible_spare_part():
    item=parse_detail(card("<p>280600 (с НДС)</p>"),source_url=URL)
    item=replace(item,technical_properties=(("Мощность двигателя, кВт","0.25"),("Напряжение питающей сети, В","380")))
    target={"sterbrust_product_id":"10","model_raw":"СЗТП 600Т","active":"Y",
            "category_name":"Станки для дисковых пил","category_path":"Станки для дисковых пил",
            "properties":[{"property_code":"MOSH","value_flat":"250"},{"property_code":"NAPR","value_flat":"380"}],"name":"Станок"}
    spare={**target,"name":"Оснастка для СЗТП600Т","model_raw":"","category_name":"Оснастка","sterbrust_product_id":"11"}
    assert links_for(item,[target,spare])[:2]==("REVIEW_CATALOG_CANDIDATE",["10"])
    assert links_for(item,[{**target,"model_raw":"СЗТП600Т ПЛЮС"}])[0]=="REVIEW_NO_UNIQUE_IDENTITY"
