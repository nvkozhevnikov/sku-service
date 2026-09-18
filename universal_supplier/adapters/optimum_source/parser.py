from __future__ import annotations

import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

from lxml import etree, html

from .constants import BASE_URL, COLLECTION_PATHS, SUPPLIER_CODE
from .models import (
    CanonicalSourceProduct, Category, Commercial, Description, Document, Identity,
    Image, PackageItem, Property, Relation, Video,
)
from .normalize import availability, clean_text, decimal_price, normalize_url, unit_from_name


def _class(name: str) -> str:
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _text(node) -> str | None:
    return clean_text(" ".join(node.itertext())) if node is not None else None


def _first(nodes):
    return nodes[0] if nodes else None


class OptimumProductParser:
    def document(self, source: str):
        return html.fromstring(source)

    def is_product(self, source: str) -> bool:
        return bool(self.page_evidence(source)["is_product"])

    def page_evidence(self, source: str) -> dict:
        doc = self.document(source)
        main = _first(doc.xpath(f"//*[{_class('c-catalog-element')}]"))
        if main is None:
            main = _first(doc.xpath(f"//*[{_class('catalog-element-body')}]/ancestor::*[{_class('c-catalog-element')}][1]"))
        scope = main if main is not None else doc
        flags = {
            "has_h1": bool(doc.xpath("//h1")),
            "has_main_product_container": main is not None,
            "has_product_schema": bool(scope.xpath(".//*[@itemtype='https://schema.org/Product' or @itemtype='http://schema.org/Product']")),
            "has_article": bool(scope.xpath(f".//*[{_class('catalog-element-article-value')}]")),
            "has_gallery": bool(scope.xpath(f".//*[{_class('catalog-element-gallery-pictures')}]")),
            "has_price": bool(scope.xpath(f".//*[{_class('catalog-element-price-current')} or {_class('catalog-element-price')}] | .//*[@itemprop='price']")),
            "has_availability": bool(scope.xpath(f".//*[{_class('catalog-element-quantity')} or {_class('catalog-element-quantity-value-text')}]")),
            "has_description": bool(scope.xpath(".//*[@data-id='description']//*[@itemprop='description'] | .//*[@data-role='section.content.item' and @data-id='description']")),
            "has_properties": bool(scope.xpath(".//*[@data-id='properties']")),
            "has_product_controls": bool(scope.xpath(".//*[@data-compare-id or @data-item or contains(@class,'catalog-element-buy')]")),
            "has_internal_product_id": self._internal_product_id(scope) is not None,
        }
        support = sum(flags[k] for k in (
            "has_article", "has_gallery", "has_price", "has_availability", "has_description",
            "has_properties", "has_product_controls", "has_internal_product_id"))
        is_product = flags["has_h1"] and flags["has_main_product_container"] and support >= 2
        flags.update({
            "support_score": support,
            "category_cards": len(doc.xpath(f"//*[{_class('catalog-section-item')}]")),
            "category_sections": len(doc.xpath("//a[contains(@class,'catalog-section-list-item') or contains(@class,'catalog-section-item-name')]")),
            "has_category_container": bool(doc.xpath(f"//*[{_class('c-catalog-section')}]")),
            "is_product": is_product,
        })
        return flags

    def is_category(self, source: str) -> bool:
        evidence = self.page_evidence(source)
        return not evidence["is_product"] and bool(evidence["has_h1"] and (
            evidence["has_category_container"] or evidence["category_cards"] or evidence["category_sections"]))

    @staticmethod
    def _internal_product_id(scope) -> str | None:
        for node in scope.xpath(".//*[@data-data] | self::*[@data-data]"):
            raw = node.get("data-data")
            if not raw:
                continue
            try:
                value = json.loads(raw).get("id")
            except (ValueError, AttributeError):
                value = None
            if value is not None and str(value).isdigit():
                return str(value)
        values = scope.xpath(".//*[@data-compare-id]/@data-compare-id | .//*[@data-item]/@data-item")
        return next((str(value) for value in values if str(value).isdigit()), None)

    def parse_identity(self, doc, source_url: str, final_url: str | None = None) -> Identity:
        name = _text(_first(doc.xpath("//h1")))
        sku = _text(_first(doc.xpath(f"//*[{_class('catalog-element-article-value')}]")))
        canonical = _first(doc.xpath("//link[contains(concat(' ',normalize-space(@rel),' '),' canonical ')]/@href"))
        canonical = normalize_url(canonical or final_url or source_url)
        main = _first(doc.xpath(f"//*[{_class('c-catalog-element')}]"))
        internal_id = self._internal_product_id(main if main is not None else doc)
        brand = manufacturer = None
        for row in doc.xpath(f"//*[{_class('catalog-element-properties-detail-item')}]"):
            key = _text(_first(row.xpath(f".//*[{_class('catalog-element-properties-detail-item-name')}]")))
            if key and key.lower() in {"бренд", "торговая марка"}:
                brand = _text(_first(row.xpath(f".//*[{_class('catalog-element-properties-detail-item-value')}]")))
            if key and key.lower() in {"производитель", "изготовитель"}:
                manufacturer = _text(_first(row.xpath(f".//*[{_class('catalog-element-properties-detail-item-value')}]")))
        for script in doc.xpath("//script[@type='application/ld+json']/text()"):
            try:
                payload = json.loads(script)
            except ValueError:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for candidate in candidates:
                if not isinstance(candidate, dict) or candidate.get("@type") != "Product":
                    continue
                def structured_name(value):
                    return value.get("name") if isinstance(value, dict) else value if isinstance(value, str) else None
                brand = brand or clean_text(structured_name(candidate.get("brand")))
                manufacturer = manufacturer or clean_text(structured_name(candidate.get("manufacturer")))
        external_id = internal_id or canonical
        external_id_type = "internal_product_id" if internal_id else "canonical_url"
        return Identity(external_id=external_id, external_id_type=external_id_type,
                        internal_product_id=internal_id, sku=sku, name=name,
                        brand_raw=brand, manufacturer_raw=manufacturer,
                        source_url=normalize_url(source_url), canonical_url=canonical)

    def parse_price_raw(self, doc) -> str | None:
        main = _first(doc.xpath(f"//*[{_class('c-catalog-element')}]"))
        scope = main if main is not None else doc
        node = _first(scope.xpath(f".//*[{_class('catalog-element-price-current')}]"))
        if node is None:
            node = _first(scope.xpath(".//*[@itemprop='price']"))
        return clean_text(node.get("content") or _text(node)) if node is not None else None

    def parse_commercial(self, doc) -> Commercial:
        product = _first(doc.xpath("//*[@itemtype='https://schema.org/Product' or @itemtype='http://schema.org/Product']"))
        if product is None:
            product = doc
        current = self.parse_price_raw(doc)
        currency = _first(product.xpath(".//*[@itemprop='priceCurrency']/@content")) or "RUB"
        old_node = _first(product.xpath(f".//*[{_class('catalog-element-price-base')} or {_class('catalog-element-price-discount-old')}]"))
        old_price = decimal_price(_text(old_node))
        price = decimal_price(current)
        if old_price == price:
            old_price = None
        quantity_node = _first(product.xpath(f".//*[{_class('catalog-element-quantity')} or {_class('catalog-element-quantity-value-text')}]"))
        raw = _text(quantity_node)
        if not raw:
            candidates = doc.xpath("//*[contains(translate(normalize-space(string(.)),'ПОДЗАКВНАЛИЧИ','подзаквналичи'),'под заказ') or contains(normalize-space(string(.)),'В наличии') or contains(normalize-space(string(.)),'СНЯТ С ПРОИЗВОДСТВА')]")
            candidates = [x for x in candidates if len(list(x.iter())) < 8]
            raw = _text(candidates[-1]) if candidates else None
        normalized, quantity = availability(raw)
        return Commercial(price=price, old_price=old_price, currency=currency,
                          availability_raw=raw, availability_normalized=normalized, quantity=quantity)

    def parse_description(self, doc) -> Description:
        node = _first(doc.xpath("//*[@data-role='section.content.item' and @data-id='description']//*[@itemprop='description']"))
        if node is None:
            node = _first(doc.xpath("//*[@itemprop='description']"))
        if node is None:
            return Description()
        return Description(text=_text(node), html=etree.tostring(node, encoding="unicode", method="html"))

    def parse_properties(self, doc) -> list[Property]:
        rows = doc.xpath(f"//*[@data-id='properties']//*[{_class('catalog-element-properties-detail-item')}]")
        seen, result = set(), []
        for row in rows:
            name_node = _first(row.xpath(f".//*[{_class('catalog-element-properties-detail-item-name')}]"))
            value_node = _first(row.xpath(f".//*[{_class('catalog-element-properties-detail-item-value')}]"))
            name, value = _text(name_node), _text(value_node)
            if not name or value is None:
                continue
            key = (name, value)
            if key in seen:
                continue
            seen.add(key)
            result.append(Property(raw_name=name, raw_value=value, raw_unit=unit_from_name(name),
                                   raw_value_html=etree.tostring(value_node, encoding="unicode", method="html"),
                                   position=len(result) + 1, is_empty_like=value.strip() in {"", "-", "—"}))
        return result

    def parse_categories(self, doc) -> list[Category]:
        nodes = doc.xpath(f"(//*[{_class('breadcrumb-wrapper')}])[last()]//*[self::a or self::span][{_class('breadcrumb-link')} or {_class('breadcrumb-item')}] | (//*[{_class('breadcrumb-wrapper')}])[last()]//a")
        result, seen = [], set()
        for node in nodes:
            name = _text(node)
            if not name or name in seen:
                continue
            seen.add(name)
            url = normalize_url(node.get("href")) if node.tag.lower() == "a" and node.get("href") else None
            result.append(Category(name=name, url=url, position=len(result) + 1))
        if result and result[-1].name == _text(_first(doc.xpath("//h1"))):
            result.pop()
        return result

    def parse_images(self, doc) -> list[Image]:
        nodes = doc.xpath(f"//*[{_class('catalog-element-gallery-pictures')}]//a[@href] | //*[{_class('catalog-element-gallery-pictures')}]//img")
        result, seen = [], set()
        for node in nodes:
            raw = node.get("href") or node.get("data-original") or node.get("data-src") or node.get("src")
            url = normalize_url(raw)
            if not url or url in seen or "/upload/" not in url or "no_photo" in url.lower():
                continue
            seen.add(url)
            result.append(Image(source_url=url, position=len(result) + 1, is_primary=not result,
                                alt=clean_text(node.get("alt")), title=clean_text(node.get("title"))))
        return result

    def parse_documents(self, doc) -> list[Document]:
        result, seen = [], set()
        section = _first(doc.xpath("//*[@data-role='section.content']"))
        nodes = doc.xpath("//a[re:test(@href, '\\.(pdf|docx?|xlsx?)(?:$|\\?)', 'i')]",
                          namespaces={"re": "http://exslt.org/regular-expressions"})
        if section is not None:
            nodes += section.xpath(".//a[@href]")
        for node in nodes:
            href = node.get("href", "")
            title = _text(node)
            if not re.search(r"\.(?:pdf|docx?|xlsx?)(?:$|\?)", href, re.I) and not (title and re.search(r"паспорт|инструкц|сертификат|каталог|руководство", title, re.I)):
                continue
            url = normalize_url(href)
            if not url or url in seen:
                continue
            seen.add(url)
            low = (title or href).lower()
            dtype = next((k for k in ("passport", "manual", "certificate", "catalog") if {"passport":"паспорт", "manual":"инструкц", "certificate":"сертификат", "catalog":"каталог"}[k] in low), "document")
            result.append(Document(source_url=url, title=title, document_type=dtype, position=len(result) + 1))
        return result

    def parse_videos(self, doc) -> list[Video]:
        result, seen = [], set()
        section = _first(doc.xpath("//*[@data-role='section.content']"))
        if section is None:
            section = doc
        for node in section.xpath(".//iframe[@src] | .//video[@src] | .//video/source[@src]"):
            url = normalize_url(node.get("src"))
            if not url or url in seen:
                continue
            seen.add(url)
            host = urlsplit(url).hostname or ""
            provider = "youtube" if "youtu" in host else "rutube" if "rutube" in host else host
            result.append(Video(source_url=url, provider=provider, title=clean_text(node.get("title")), position=len(result) + 1))
        return result

    def parse_package_contents(self, doc) -> list[PackageItem]:
        section = _first(doc.xpath("//*[@data-role='section.content.item' and @data-id='complect']"))
        if section is None:
            return []
        nodes = section.xpath(".//li")
        if not nodes:
            nodes = section.xpath(f".//*[{_class('catalog-element-complect')}]/*")
        values = [_text(x) for x in nodes]
        return [PackageItem(text=x, position=i + 1) for i, x in enumerate(dict.fromkeys(v for v in values if v))]

    def parse_related_products(self, doc) -> list[Relation]:
        result, seen = [], set()
        for relation_type, data_id in (("accessory", "accessories"), ("related", "related")):
            for node in doc.xpath(f"//*[@data-role='section.content.item' and @data-id='{data_id}']//a[@href]"):
                url = normalize_url(node.get("href"))
                if not url or url in seen or "/catalog/" not in url:
                    continue
                name_node = _first(node.xpath(f".//*[{_class('catalog-products-viewed-name-wrapper')}]"))
                name = _text(name_node) or clean_text(node.get("title"))
                if not name:
                    continue
                seen.add(url)
                price_node = _first(node.xpath(f".//*[{_class('catalog-products-viewed-price')}]"))
                result.append(Relation(relation_type=relation_type, target_name=name, target_url=url,
                                       price=decimal_price(_text(price_node)), position=len(result) + 1))
        return result

    def parse_product(self, source: str, source_url: str, final_url: str | None = None,
                      collections: list[str] | None = None) -> CanonicalSourceProduct:
        doc = self.document(source)
        identity = self.parse_identity(doc, source_url, final_url)
        commercial = self.parse_commercial(doc)
        properties = self.parse_properties(doc)
        discovered_collections = list(dict.fromkeys(collections or []))
        path = urlsplit(identity.canonical_url or source_url).path
        for prefix, value in COLLECTION_PATHS.items():
            if path.startswith(prefix) and value not in discovered_collections:
                discovered_collections.append(value)
        return CanonicalSourceProduct(
            supplier=SUPPLIER_CODE, identity=identity, commercial=commercial,
            description=self.parse_description(doc), categories=self.parse_categories(doc),
            properties=properties, images=self.parse_images(doc), documents=self.parse_documents(doc),
            videos=self.parse_videos(doc), package_contents=self.parse_package_contents(doc),
            related_products=self.parse_related_products(doc), collections=discovered_collections,
            raw_data={"source_article_text": identity.sku, "source_stock_text": commercial.availability_raw,
                      "internal_product_id": identity.internal_product_id,
                      "external_id_type": identity.external_id_type,
                      "source_price_text": self.parse_price_raw(doc), "source_sections_found": [x for x, ok in {
                          "description": bool(self.parse_description(doc).text), "properties": bool(properties),
                          "complect": bool(self.parse_package_contents(doc)), "accessories": bool(self.parse_related_products(doc))}.items() if ok],
                      "canonical_source": "html_canonical" if doc.xpath("//link[@rel='canonical']") else "fallback_final_url"},
        )
