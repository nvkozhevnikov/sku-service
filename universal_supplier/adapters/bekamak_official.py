"""Evidence-bound Bekamak manufacturer card parser.

The official source has its own namespace. Its JSON-LD Offer without a
numeric price is *not* evidence of a commercial price or live stock.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import urlparse

from universal_supplier.commercial import PriceExtraction, PriceSourceKind, PriceState, ReadOnlySupplierProduct
from universal_supplier.commercial_discovery import normalise_model


class _OfficialDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1_depth = 0
        self.h1_text: list[str] = []
        self.script_depth = 0
        self.script_text: list[str] = []
        self.json_ld: list[str] = []
        self.technical_depth = 0
        self.technical_rows: list[tuple[str, str]] = []
        self._rows: list[list[str]] = []
        self._cells: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "div" and dict(attrs).get("id") == "teknik-detaylar":
            self.technical_depth = 1
        elif tag == "div" and self.technical_depth:
            self.technical_depth += 1
        if self.technical_depth and tag == "tr":
            self._rows.append([])
        elif self.technical_depth and tag in {"td", "th"} and self._rows:
            self._cells.append([])
        if tag == "h1":
            self.h1_depth += 1
        elif tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.script_depth += 1
            self.script_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.technical_depth and tag in {"td", "th"} and self._cells and self._rows:
            self._rows[-1].append(" ".join("".join(self._cells.pop()).split()))
        elif self.technical_depth and tag == "tr" and self._rows:
            cells = self._rows.pop()
            if len(cells) == 2 and all(cells):
                self.technical_rows.append((cells[0], cells[1]))
        if tag == "div" and self.technical_depth:
            self.technical_depth -= 1
        if tag == "h1" and self.h1_depth:
            self.h1_depth -= 1
        elif tag == "script" and self.script_depth:
            self.script_depth -= 1
            self.json_ld.append("".join(self.script_text))

    def handle_data(self, data: str) -> None:
        if self.technical_depth and self._cells:
            self._cells[-1].append(data)
        if self.h1_depth:
            self.h1_text.append(data)
        if self.script_depth:
            self.script_text.append(data)


def _products(value):
    if isinstance(value, list):
        for item in value:
            yield from _products(item)
    elif isinstance(value, dict):
        kinds = value.get("@type")
        if kinds == "Product" or isinstance(kinds, list) and "Product" in kinds:
            yield value
        if "@graph" in value:
            yield from _products(value["@graph"])


def _breadcrumb_category(value) -> tuple[str, str | None]:
    values = value if isinstance(value, list) else [value]
    for entry in values:
        graph = entry.get("@graph", []) if isinstance(entry, dict) else []
        for node in graph if isinstance(graph, list) else [graph]:
            if not isinstance(node, dict) or node.get("@type") != "BreadcrumbList":
                continue
            elements = node.get("itemListElement") or []
            categories = [(str(item.get("name") or "").strip(), str(item.get("item") or "").strip())
                          for item in elements if isinstance(item, dict) and item.get("item")]
            if len(categories) >= 2 and categories[-1][1].startswith("https://www.bekamak.com/"):
                return categories[-1]
    return "", None


def parse_bekamak_official_detail(source: str, *, source_url: str | None = None) -> ReadOnlySupplierProduct:
    """Parse one proven official product page; reject mismatched model evidence."""
    if not source_url:
        raise ValueError("official product URL is required")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in {"bekamak.com", "www.bekamak.com"}:
        raise ValueError("official product URL is outside bekamak.com")
    path = parsed.path.rstrip("/").split("/")
    if len(path) != 4 or path[1] != "urun" or path[3] != "en" or not re.fullmatch(r"[a-zA-Z0-9_-]+", path[2]):
        raise ValueError("official product URL has unexpected path")
    slug = path[2]
    document = _OfficialDocument()
    document.feed(source)
    document.close()
    title = " ".join(" ".join(document.h1_text).split())
    if not title:
        raise ValueError("official primary title is missing")
    products = []
    category = ("", None)
    for script in document.json_ld:
        try:
            parsed_ld = json.loads(script)
            products.extend(_products(parsed_ld))
            found_category = _breadcrumb_category(parsed_ld)
            if found_category[0]:
                category = found_category
        except (TypeError, ValueError):
            continue
    matching = [item for item in products if item.get("url", "").rstrip("/") == source_url.rstrip("/")]
    if len(matching) != 1:
        raise ValueError("official JSON-LD product identity is not unique")
    record = matching[0]
    model = normalise_model(title)
    if normalise_model(str(record.get("name") or "")) != model or normalise_model(str(record.get("sku") or "")) != model:
        raise ValueError("official title, name and SKU disagree")
    if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", model):
        raise ValueError("official model token is not confirmed")
    properties = tuple(
        (str(item["name"]).strip(), str(item["value"]).strip())
        for item in record.get("additionalProperty", [])
        if isinstance(item, dict) and item.get("name") and item.get("value")
    )
    if not properties:
        properties = tuple(document.technical_rows)
        machine_spec = re.compile(r"cutting capacity|blade|motor|power requirement|saw speed|cutting speed", re.I)
        if not any(machine_spec.search(name) for name, _ in properties):
            raise ValueError("official equipment technical properties are not confirmed")
    image = record.get("image")
    images = (image,) if isinstance(image, str) and image.startswith("https://www.bekamak.com/") else ()
    price = PriceExtraction(
        PriceState.MISSING, None, None, None, "", PriceSourceKind.NONE, None,
        ("official_reference_no_verified_numeric_price",), model, source_url, "official_json_ld_product",
    )
    return ReadOnlySupplierProduct(
        supplier="beka_mak_tr", source_url=source_url, name=title,
        supplier_model=model, manufacturer_article=model, site_internal_id=slug,
        availability=None, price=price,
        diagnostics=("manufacturer_reference_not_commercial_offer", "official_schema_availability_not_stock_proof"),
        description_text=str(record.get("description") or "").strip(),
        technical_properties=properties, source_images=images,
        source_category=category[0], source_category_url=category[1],
        raw_supplier_model=title,
    )
