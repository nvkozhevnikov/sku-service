from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from universal_supplier.http import HttpTransport
from universal_supplier.models import (
    CategoryNode, DiscoveryResult, FetchRecord, MediaItem, OptionValue,
    ProductCard, ProductOption, PropertyValue,
)


def parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9,.-]", "", str(value)).replace(",", ".")
    try:
        return Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None


def normalize_currency(value: object) -> str:
    text = str(value or "").strip().upper()
    return "RUB" if text in {"RUB", "RUR", "₽", "РУБ", "РУБ."} else text[:3]


def normalize_availability(value: object) -> str:
    text = str(value or "").lower()
    if "instock" in text or "в наличии" in text:
        return "in_stock"
    if "outofstock" in text or "нет в наличии" in text:
        return "out_of_stock"
    if "preorder" in text or "предзаказ" in text:
        return "preorder"
    if "backorder" in text or "под заказ" in text:
        return "backorder"
    return "unknown"


def _json_ld(soup: BeautifulSoup) -> list[dict]:
    result: list[dict] = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(node.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        values = value if isinstance(value, list) else [value]
        result.extend(item for item in values if isinstance(item, dict))
    return result


class PartnerStAdapter:
    code = "partner_st"
    base_url = "https://partner-st.ru/"
    sitemap_url = "https://partner-st.ru/sitemap.xml"

    def discover(self, transport: HttpTransport) -> DiscoveryResult:
        index_fetch = transport.get(self.sitemap_url)
        if index_fetch.status_code != 200:
            raise RuntimeError(f"sitemap index HTTP {index_fetch.status_code}")
        root = ET.fromstring(index_fetch.text)
        sitemap_urls = tuple(dict.fromkeys(
            node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text
        ))
        product_sitemaps = tuple(url for url in sitemap_urls if PurePosixPath(urlparse(url).path).name.lower().startswith("products"))
        if not product_sitemaps and root.tag.endswith("urlset"):
            product_sitemaps = (self.sitemap_url,)
        product_urls: list[str] = []
        for sitemap_url in product_sitemaps:
            fetched = transport.get(sitemap_url)
            if fetched.status_code != 200:
                raise RuntimeError(f"product sitemap HTTP {fetched.status_code}: {sitemap_url}")
            sitemap_root = ET.fromstring(fetched.text)
            product_urls.extend(
                node.text.strip() for node in sitemap_root.iter() if node.tag.endswith("loc") and node.text
            )
        unique_urls = tuple(dict.fromkeys(product_urls))
        return DiscoveryResult(self.sitemap_url, sitemap_urls, product_sitemaps, unique_urls)

    def parse_product(self, fetch: FetchRecord) -> ProductCard:
        soup = BeautifulSoup(fetch.text, "html.parser")
        objects = _json_ld(soup)
        product = next((item for item in objects if item.get("@type") == "Product"), {})
        breadcrumbs = next((item for item in objects if item.get("@type") == "BreadcrumbList"), {})
        product_id_node = soup.find("input", attrs={"name": re.compile(r"^product_data\[\d+\]\[product_id\]$")})
        if not product_id_node or not str(product_id_node.get("value", "")).isdigit():
            raise ValueError("Partner-ST product_id not found")
        external_id = str(product_id_node["value"])
        canonical_node = soup.find("link", rel=lambda value: value and "canonical" in value)
        canonical = urljoin(fetch.final_url, canonical_node.get("href")) if canonical_node and canonical_node.get("href") else fetch.final_url
        name = str(product.get("name") or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")).strip()
        sku = str(product.get("sku") or "").strip()
        offer = product.get("offers") if isinstance(product.get("offers"), dict) else {}
        availability_raw = str(offer.get("availability") or "")
        description_text = str(product.get("description") or "").strip()
        # The surrounding description tab contains volatile recommendation forms
        # and security hashes. JSON-LD is the stable raw description source.
        description_html = ""

        properties = []
        for position, row in enumerate(soup.select(".ty-product-feature")):
            label = row.select_one(".ty-product-feature__label")
            value = row.select_one(".ty-product-feature__value")
            if label and value:
                raw_name = label.get_text(" ", strip=True).rstrip(":")
                raw_value = value.get_text(" ", strip=True)
                unit_match = re.search(r",\s*([а-яa-zA-Z°/%]+)$", raw_name)
                properties.append(PropertyValue(raw_name, raw_value, unit_match.group(1) if unit_match else "", position))

        media: list[MediaItem] = []
        images: list[str] = []
        raw_image = product.get("image")
        if isinstance(raw_image, str):
            images.append(raw_image)
        elif isinstance(raw_image, list):
            images.extend(str(item) for item in raw_image)
        for node in soup.select("a.cm-image-previewer[href], meta[property='og:image'][content]"):
            images.append(str(node.get("href") or node.get("content") or ""))
        for position, url in enumerate(dict.fromkeys(urljoin(fetch.final_url, item) for item in images if item)):
            media.append(MediaItem("image", url, position=position, is_primary=position == 0))
        document_extensions = re.compile(r"\.(?:pdf|docx?|xlsx?|zip)(?:\?|$)", re.I)
        for node in soup.find_all("a", href=True):
            url = urljoin(fetch.final_url, node["href"])
            if document_extensions.search(url) or "dispatch=attachments.getfile" in url:
                media.append(MediaItem("document", url, node.get_text(" ", strip=True), len(media)))
        for node in soup.select("iframe[src], video[src], source[src]"):
            url = urljoin(fetch.final_url, node.get("src"))
            if url:
                media.append(MediaItem("video", url, node.get("title", ""), len(media)))
        media = list(dict.fromkeys(media))

        options: list[ProductOption] = []
        for option_position, group in enumerate(soup.select(".ut2-pb__options .ty-product-options__item")):
            label = group.select_one(".ty-product-options__item-label")
            if not label:
                continue
            values: list[OptionValue] = []
            for value_position, input_node in enumerate(group.select("input[data-ca-variant-id]")):
                label_node = group.find("label", attrs={"for": input_node.get("id")})
                value_text = label_node.get_text(" ", strip=True) if label_node else str(input_node.get("value", ""))
                target_url = urljoin(fetch.final_url, str(input_node.get("data-ca-product-url") or ""))
                selected = input_node.has_attr("checked")
                classification = "INFORMATIONAL" if selected or target_url.rstrip("/") == canonical.rstrip("/") else "NAVIGATION_TO_PRODUCT"
                values.append(OptionValue(value_text, target_url, str(input_node.get("data-ca-variant-id", "")), classification, selected, value_position))
            if values:
                options.append(ProductOption(label.get_text(" ", strip=True).rstrip(":"), tuple(values), option_position))

        categories: list[CategoryNode] = []
        for item in breadcrumbs.get("itemListElement", []) if isinstance(breadcrumbs, dict) else []:
            if not isinstance(item, dict) or int(item.get("position", 0)) <= 1:
                continue
            categories.append(CategoryNode(str(item.get("name", "")), str(item.get("item", "")), int(item.get("position", 0)) - 2))
        if categories and categories[-1].url.rstrip("/") == canonical.rstrip("/"):
            categories.pop()

        brand = ""
        brand_value = product.get("brand")
        if isinstance(brand_value, dict):
            brand = str(brand_value.get("name", ""))
        elif brand_value:
            brand = str(brand_value)
        if not brand and name.lower().startswith("partner "):
            brand = "Partner"
        if not brand:
            for item in properties:
                if item.name.strip().lower() in {"бренд", "марка"}:
                    brand = item.value
                    break
        manufacturer = ""
        for item in properties:
            if item.name.strip().lower() in {"производитель", "изготовитель"}:
                manufacturer = item.value
                break
        quantity_node = soup.select_one(f"input[name='product_data[{external_id}][amount]']")
        quantity = parse_decimal(quantity_node.get("max")) if quantity_node and quantity_node.get("max") else None
        if quantity is None:
            stock_node = soup.select_one(f"#qty_in_stock_{external_id}")
            quantity = parse_decimal(stock_node.get_text(" ", strip=True)) if stock_node else None
        old_price_node = soup.select_one(f"#old_price_update_{external_id} .ty-list-price, .ty-list-price")

        return ProductCard(
            supplier_code=self.code, external_id=external_id, sku=sku, name=name,
            requested_url=fetch.requested_url, final_url=fetch.final_url, canonical_url=canonical,
            http_status=fetch.status_code, redirect_urls=fetch.redirects,
            price=parse_decimal(offer.get("price")),
            old_price=parse_decimal(old_price_node.get_text(" ", strip=True)) if old_price_node else None,
            currency=normalize_currency(offer.get("priceCurrency")),
            availability_raw=availability_raw, availability_normalized=normalize_availability(availability_raw),
            quantity=quantity, description_text=description_text, description_html=description_html,
            brand=brand, manufacturer=manufacturer, properties=tuple(properties), media=tuple(media), options=tuple(options),
            categories=tuple(categories),
            raw_data={"json_ld_product": product, "fetch_attempts": fetch.attempts, "elapsed_ms": fetch.elapsed_ms,
                      "source_kind": "supplier_html"},
            price_type="unknown",
            price_raw=str(offer.get("price") or ""),
            price_source="JSON-LD offers.price",
        )
