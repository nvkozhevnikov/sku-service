from pathlib import Path

from universal_supplier.adapters.optimum_source.adapter import OptimumAdapter
from universal_supplier.adapters.optimum_source.models import CanonicalSourceProduct
from universal_supplier.adapters.optimum_source.normalize import availability, decimal_price, normalize_url
from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
from universal_supplier.adapters.optimum import _availability_for_database


URL = "https://optimum.su/catalog/nastolnye/tu-2406-230/"
HTML = """
<html><head><link rel="canonical" href="https://optimum.su/catalog/nastolnye/tu-2406-230/?utm_source=x"></head><body>
<div class="c-catalog-element" itemtype="https://schema.org/Product" data-data='{"id":"1001"}'>
 <h1>Токарный станок OPTIturn TU 2406 (230 В)</h1>
 <div class="catalog-element-article-value">3420350</div>
 <meta itemprop="price" content="191487"><meta itemprop="priceCurrency" content="RUB">
 <div class="catalog-element-price-base">200 000 ₽</div>
 <div class="catalog-element-quantity"><span class="catalog-element-quantity-value-text">В наличии 5</span></div>
 <div class="catalog-element-gallery-pictures"><a href="/upload/iblock/a/product.jpg"><img alt="TU 2406"></a></div>
</div>
<div class="breadcrumb-wrapper"><a class="breadcrumb-link" href="/catalog/">Каталог</a><a class="breadcrumb-link" href="/catalog/tokarnye/">Токарные станки</a></div>
<div data-role="section.content">
 <div data-role="section.content.item" data-id="description"><div itemprop="description"><p>Описание станка.</p></div></div>
 <div data-role="section.content.item" data-id="properties"><div class="catalog-element-properties-detail">
  <div class="catalog-element-properties-detail-item"><div class="catalog-element-properties-detail-item-name">Мощность, кВт</div><div class="catalog-element-properties-detail-item-value">0,75</div></div>
  <div class="catalog-element-properties-detail-item"><div class="catalog-element-properties-detail-item-name">Мощность, кВт</div><div class="catalog-element-properties-detail-item-value">0,75</div></div>
  <div class="catalog-element-properties-detail-item"><div class="catalog-element-properties-detail-item-name">Значение</div><div class="catalog-element-properties-detail-item-value">-</div></div>
 </div></div>
 <div data-role="section.content.item" data-id="complect"><div class="catalog-element-complect"><ul><li>Патрон</li><li>Ключ</li></ul></div></div>
 <div data-role="section.content.item" data-id="accessories"><a href="/catalog/aksessuary/patron/"><span class="catalog-products-viewed-name-wrapper">Патрон</span><span class="catalog-products-viewed-price">10 000 ₽</span></a></div>
 <iframe src="https://youtube.com/embed/abc" title="Обзор"></iframe>
</div>
<a href="/upload/manual.pdf">Инструкция</a>
</body></html>
"""

FIXTURES = Path(__file__).with_name("fixtures")
QW125_URL = "https://optimum.su/catalog/tiski_dlya_frezernykh_stankov/tryekhosevye_stanochnye_tiski_qw125/"
QW100_URL = "https://optimum.su/catalog/tiski_dlya_frezernykh_stankov/tryekhosevye_stanochnye_tiski_qw100/"
CATEGORY_HTML = """
<html><head><link rel="canonical" href="https://optimum.su/catalog/tiski_dlya_frezernykh_stankov/"></head><body>
<h1>Тиски для фрезерных станков</h1>
<div class="catalog-section-item" itemtype="https://schema.org/Product" data-id="659">
  <a class="catalog-section-item-name" href="/catalog/tiski/example/">Товар</a>
  <meta itemprop="price" content="10821">
</div>
</body></html>
"""
EMPTY_CATEGORY_HTML = """
<html><head><link rel="canonical" href="https://optimum.su/catalog/empty/"></head><body>
<h1>Пустая категория</h1><div class="c-catalog-section"><div class="catalog-section-items"></div></div>
</body></html>
"""


def product():
    return OptimumProductParser().parse_product(HTML, URL)


def test_product_identity():
    assert product().identity.external_id == "1001"
    assert product().identity.external_id_type == "internal_product_id"


def test_article_parse():
    assert product().identity.sku == "3420350"


def test_price_parse():
    assert product().commercial.price == "191487.00"


def test_old_price_parse():
    assert product().commercial.old_price == "200000.00"


def test_zero_price_not_real():
    assert decimal_price("0 ₽") is None


def test_in_stock_quantity():
    assert availability("В наличии 5") == ("in_stock", 5)


def test_on_request():
    assert availability("Под заказ") == ("on_request", None)


def test_on_request_maps_to_shared_database_vocabulary():
    assert _availability_for_database("on_request") == "backorder"
    assert _availability_for_database("discontinued") == "discontinued"
    assert _availability_for_database(None) == "unknown"


def test_description():
    assert product().description.text == "Описание станка."


def test_properties_table():
    assert product().properties[0].raw_name == "Мощность, кВт"


def test_property_dedup():
    assert len(product().properties) == 2


def test_package_contents():
    assert [x.text for x in product().package_contents] == ["Патрон", "Ключ"]


def test_accessories_relation():
    assert product().related_products[0].relation_type == "accessory"


def test_images():
    assert product().images[0].is_primary


def test_category_breadcrumb():
    assert [x.name for x in product().categories] == ["Каталог", "Токарные станки"]


def test_promotion_not_duplicate():
    p = OptimumProductParser().parse_product(HTML, URL, collections=["promotion", "promotion"])
    assert p.collections == ["promotion"]


def test_voltage_variants_not_merged():
    html_400 = HTML.replace("3420350", "3420353").replace("230 В", "400 В").replace('"1001"', '"1002"')
    other = OptimumProductParser().parse_product(html_400, URL.replace("230", "400"))
    assert product().identity.external_id != other.identity.external_id


def test_canonical_normalization():
    assert normalize_url("/catalog/x/?utm_source=a#z") == "https://optimum.su/catalog/x/"


def test_output_schema():
    assert CanonicalSourceProduct.model_validate(product().model_dump()).supplier == "optimum"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_product_without_sku_detected():
    parser = OptimumProductParser()
    assert parser.is_product(_fixture("qw125.html"))


def test_product_without_sku_schema_valid():
    parsed = OptimumProductParser().parse_product(_fixture("qw125.html"), QW125_URL)
    assert CanonicalSourceProduct.model_validate(parsed.model_dump()).identity.sku is None


def test_product_without_sku_fallback_identity():
    parsed = OptimumProductParser().parse_product(_fixture("qw125.html"), QW125_URL)
    assert (parsed.identity.external_id, parsed.identity.external_id_type) == ("5472", "internal_product_id")


def test_qw125_not_category():
    parser = OptimumProductParser()
    assert parser.is_product(_fixture("qw125.html")) and not parser.is_category(_fixture("qw125.html"))


def test_qw100_not_category():
    parser = OptimumProductParser()
    source = _fixture("qw100.html")
    parsed = parser.parse_product(source, QW100_URL)
    assert parser.is_product(source) and not parser.is_category(source)
    assert parsed.identity.external_id == "5471"


def test_category_not_product():
    parser = OptimumProductParser()
    assert parser.is_category(CATEGORY_HTML)
    assert not parser.is_product(CATEGORY_HTML)


def test_empty_category_not_other_or_product():
    parser = OptimumProductParser()
    assert parser.is_category(EMPTY_CATEGORY_HTML)
    assert not parser.is_product(EMPTY_CATEGORY_HTML)


def test_product_alias_not_duplicate():
    first = OptimumProductParser().parse_product(_fixture("qw125.html"), QW125_URL)
    second = first.model_copy(deep=True)
    second.identity.source_url = QW125_URL + "alias/"
    results = [{"product": first, "classification": "PRODUCT", "reason": "", "audit": {}},
               {"product": second, "classification": "PRODUCT", "reason": "", "audit": {}}]
    unique, aliases, _ = OptimumAdapter._deduplicate(results)
    assert len(unique) == 1 and len(aliases) == 1


def test_category_tree_contains_no_product_urls():
    parser = OptimumProductParser()
    assert parser.is_category(CATEGORY_HTML)
    assert all(not parser.is_category(_fixture(name)) for name in ("qw125.html", "qw100.html"))


def test_raw_price_text_preserved():
    parsed = product()
    assert parsed.raw_data["source_price_text"] == "191487"
    assert parsed.commercial.price == "191487.00"
    qw = OptimumProductParser().parse_product(_fixture("qw125.html"), QW125_URL)
    assert qw.raw_data["source_price_text"] == "Цена по запросу"
    assert qw.commercial.price is None


def test_external_id_type():
    assert product().identity.external_id_type == "internal_product_id"
    assert OptimumProductParser().parse_product(
        _fixture("qw100.html"), QW100_URL).identity.external_id_type == "internal_product_id"


def test_missing_sku_is_allowed():
    parsed = OptimumProductParser().parse_product(_fixture("qw125.html"), QW125_URL)
    assert parsed.identity.sku is None
    assert parsed.parse_status == "OK"

