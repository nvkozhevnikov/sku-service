"""Catalog and own-card extraction for the Ivstankomaster storefront."""
import re
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from universal_supplier.commercial import (
    PriceExtraction, PriceSourceKind, PriceState, ReadOnlySupplierProduct,
)

CODE = "ivstankomaster"
HOST = "ivstankomaster.ru"
BASE = f"https://{HOST}"
DETAIL = re.compile(r"/item/[a-z0-9-]+/?")
CATEGORY = re.compile(r"/category(?:/[a-z0-9-]+)?/?")
AMOUNT = r"(?<!\d)(\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d{1,2})?)"
RUB = r"\s*(?:₽|руб\.?|р\.?)?\s*"
EXPLICIT_VAT = re.compile(AMOUNT + RUB + r"\(\s*с\s+НДС\s*\)", re.I)
NET = re.compile(AMOUNT + RUB + r"\(\s*без\s+НДС\s*\)", re.I)
ORDERED_PAIR = re.compile(NET.pattern + r"\s*/\s*" + AMOUNT + RUB + r"(?=\s*(?:$|\.(?!\d)))", re.I)


def checked_url(url, pattern):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in (HOST, "www." + HOST)
            or parsed.query or parsed.fragment or not pattern.fullmatch(parsed.path)):
        raise ValueError("Неподдерживаемый адрес Ивстанкомастер")
    return url


def _local_link(base, href, pattern):
    try:
        parsed = urlsplit(checked_url(urljoin(base, href), pattern))
    except ValueError:
        return None
    return BASE + parsed.path.rstrip("/")


def decode_catalog(url, body):
    checked_url(url, CATEGORY)
    dom = BeautifulSoup(body, "html.parser")
    if dom.select_one(".pagination a[href], a[rel=next]"):
        raise ValueError("Неизвестная пагинация каталога Ивстанкомастер")
    children, products = set(), set()
    for link in dom.select("a[href]"):
        if category := _local_link(url, link["href"], CATEGORY):
            if category != BASE + urlsplit(url).path.rstrip("/"):
                children.add(category)
        if product := _local_link(url, link["href"], DETAIL):
            products.add(product)
    if not products and not children:
        raise ValueError("Каталог Ивстанкомастер не распознан")
    return sorted(children), [{"url": item, "discovery_evidence": "catalog_link"} for item in sorted(products)]


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _money(raw):
    return Decimal(re.sub(r"\s", "", raw).replace(",", "."))


def _prices(own, description):
    blocks = [_text(node) for node in description.select("p,li,tr")
              if not node.select_one("p,li,tr")] if description else []
    nets = [(_money(m[1]), text) for text in blocks for m in NET.finditer(text)]
    vat = [(_money(m[1]), text) for text in blocks for m in EXPLICIT_VAT.finditer(text)]
    retail = own.get("data-retail", "").strip()
    retail = _money(retail) if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", retail) else None
    proof = "explicit_vat"
    if not vat:
        # The supplier's payment terms say two prices are shown in the order
        # without/with VAT. Accept this layout only with the labelled first
        # amount, matching checkout retail and a single higher second amount.
        vat = [(_money(m[2]), text) for text in blocks for m in ORDERED_PAIR.finditer(text)
               if _money(m[1]) == retail and _money(m[2]) > _money(m[1])]
        proof = "ordered_pair_supplier_terms"
    if len({amount for amount, _ in vat}) != 1 or len({amount for amount, _ in nets}) > 1:
        return None, "", None, nets, retail
    gross, raw = vat[0]
    net = nets[0][0] if nets else None
    if gross <= 0 or (net is not None and gross <= net) or (net and retail is not None and retail != net):
        return None, raw, None, nets, retail
    return gross, raw, proof, nets, retail


def parse_detail(html, *, source_url):
    checked_url(source_url, DETAIL)
    dom = BeautifulSoup(html, "html.parser")
    own_cards = dom.select(".item-additionally button.add_item[data-id]")
    heading = _text(dom.select_one(".breadcrumb li[aria-current=page]"))
    if len(own_cards) != 1 or not heading:
        raise ValueError("Собственная карточка Ивстанкомастер не распознана")
    own = own_cards[0]
    identifier = own["data-id"]
    if not re.fullmatch(r"[1-9][0-9]*", identifier) or heading != " ".join(own.get("data-title", "").split()):
        raise ValueError("Расхождение идентичности карточки Ивстанкомастер")
    model = " ".join(own.get("data-article", "").split()) or None
    description = dom.select_one("#item-description")
    gross, raw, proof, nets, retail = _prices(own, description)
    price = PriceExtraction(
        state=PriceState.NUMERIC_PUBLIC if gross is not None else PriceState.AMBIGUOUS,
        current_price=gross, old_price=None, currency="RUB" if gross is not None else None,
        raw_text=raw, source_kind=PriceSourceKind.PRODUCT_PRICE_DOM if proof else PriceSourceKind.NONE,
        source_path="#item-description" if proof else None,
        diagnostics=(proof or "vat_price_unconfirmed",), product_identity=identifier,
        source_url=source_url, context_path=".item-additionally",
        price_type_evidence={"classification":"unknown","status":"NOT_STATED",
                             "vat_included": gross is not None,"vat_basis":proof},
    )
    stock = own.get("data-allcount", "").strip()
    quantity = int(stock) if re.fullmatch(r"[0-9]{1,8}", stock) else None
    properties = []
    for row in dom.select(".item-attributes .item-attribute-block"):
        name = _text(row.select_one(".attribute-block-title")).rstrip(":")
        value = _text(row.select_one(".attribute-block-value"))
        if name and value:
            properties.append((name, value))
    images = []
    for image in dom.select(".item-img-slider-for img[src]"):
        url = urljoin(source_url, image["src"])
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.netloc in (HOST, "www."+HOST)
                and parsed.path.startswith("/images/uploads/") and not parsed.query and not parsed.fragment):
            images.append(url)
    manufactured = bool(re.match(r"^(СЗ[ТПМ]|Стойка|Тумба|СОЖ)", model or "", re.I)
                        or re.match(r"^(?:Станок|Тумба) ", heading, re.I))
    return ReadOnlySupplierProduct(
        supplier=CODE, source_url=source_url, name=heading, supplier_model=model,
        manufacturer_article=None, site_internal_id=identifier,
        availability=("InStock" if quantity else "OutOfStock") if quantity is not None else None,
        price=price, description_text=_text(description), technical_properties=tuple(properties),
        source_images=tuple(dict.fromkeys(images)), source_brand="Ивстанкомастер" if manufactured else "",
        source_category="Ивстанкомастер", raw_supplier_model=model, source_quantity=quantity,
        enrichment_evidence={"checkout_retail":str(retail) if retail is not None else None,
                             "net_prices":[str(amount) for amount, _ in nets],
                             "vat_basis":proof,"stock_source":"button.add_item[data-allcount]"},
    )
