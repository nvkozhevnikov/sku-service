from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

from lxml import etree, html

from universal_supplier.control_plane.rate_control import MaxRunDurationExceeded

from .constants import CATALOG_URL, COLLECTION_PATHS, SITEMAP_URL
from .fetch import HttpFetcher
from .models import DiscoveryRecord
from .normalize import clean_text, normalize_url
from .parser import OptimumProductParser, _first, _text


class OptimumDiscovery:
    def __init__(self, fetcher: HttpFetcher):
        self.fetcher = fetcher
        self.parser = OptimumProductParser()
        # Category/listing pages that could not be read.  Any entry means the
        # discovered product set may be incomplete, so deactivation is unsafe.
        self.category_page_failures: list[dict] = []

    def _category_failure(self, url: str, fetched) -> None:
        if isinstance(fetched, Exception):
            self.category_page_failures.append({"url": url, "http_status": None,
                                                "error_type": type(fetched).__name__})
        else:
            self.category_page_failures.append({"url": url, "http_status": fetched.status_code,
                                                "error_type": f"HTTP_{fetched.status_code}"})

    async def sitemap_urls(self) -> tuple[list[str], list[str]]:
        root = etree.fromstring((await self.fetcher.get(SITEMAP_URL)).text.encode())
        # A <loc> on a foreign host is never fetched: filter by host, not only by path.
        maps = [url for url in (normalize_url(str(value).strip())
                                for value in root.xpath("//*[local-name()='sitemap']/*[local-name()='loc']/text()")) if url]
        all_urls = []
        for sitemap in maps:
            child = etree.fromstring((await self.fetcher.get(sitemap)).text.encode())
            all_urls.extend(child.xpath("//*[local-name()='url']/*[local-name()='loc']/text()"))
        catalog = {normalize_url(str(u).strip()) for u in all_urls}
        return maps, sorted(u for u in catalog if u and urlsplit(u).path.startswith("/catalog/"))

    async def discover_categories(self, seed_urls: list[str] | None = None) -> list[dict]:
        """Discover taxonomy through navigation and category-section links.

        The catalog root currently may reject an ordinary GET, so a live catalog
        page from the sitemap is also accepted as a navigation seed.
        """
        seeds = [CATALOG_URL] + list(seed_urls or ())[:24]
        navigation_doc = None
        seed_failures: list[tuple[str, object]] = []
        for seed in seeds:
            try:
                page = await self.fetcher.get(seed)
            except MaxRunDurationExceeded:
                raise
            except Exception as error:
                seed_failures.append((seed, error))
                continue
            if page.status_code != 200:
                seed_failures.append((seed, page))
                continue
            candidate = html.fromstring(page.text)
            if candidate.xpath("//a[contains(@class,'menu-submenu-section-header-wrapper')]"):
                navigation_doc = candidate
                break
        if navigation_doc is None:
            # Without a navigation seed the taxonomy is incomplete.
            for seed, failure in seed_failures:
                self._category_failure(seed, failure)

        candidates: dict[str, dict] = {
            CATALOG_URL: {"category_name": "Каталог товаров", "category_url": CATALOG_URL,
                          "parent": None, "discovery_sources": "root"}
        }
        if navigation_doc is not None:
            for link in navigation_doc.xpath(
                    "//a[contains(@class,'menu-submenu-section-header-wrapper') or "
                    "contains(@class,'menu-submenu-section-item-wrapper')]"):
                url = normalize_url(link.get("href"))
                name = clean_text(" ".join(link.itertext()))
                if not url or not name:
                    continue
                parent = CATALOG_URL
                if "menu-submenu-section-item-wrapper" in (link.get("class") or ""):
                    group = link.xpath("ancestor::*[contains(@class,'menu-submenu-section-links')][1]")
                    header = group[0].xpath(".//a[contains(@class,'menu-submenu-section-header-wrapper')][1]") if group else []
                    if header:
                        parent = normalize_url(header[0].get("href")) or CATALOG_URL
                candidates[url] = {"category_name": name, "category_url": url,
                                   "parent": parent, "discovery_sources": "navigation"}

        result = [candidates[CATALOG_URL]]
        queue = [url for url in candidates if url != CATALOG_URL]
        seen = set()
        while queue:
            batch = [url for url in queue[:12] if url not in seen]
            del queue[:12]
            seen.update(batch)
            fetched_batch = await asyncio.gather(
                *(self.fetcher.get(url) for url in batch), return_exceptions=True)
            for source_url, fetched in zip(batch, fetched_batch):
                if isinstance(fetched, MaxRunDurationExceeded):
                    raise fetched
                if isinstance(fetched, Exception) or fetched.status_code != 200:
                    self._category_failure(source_url, fetched)
                    continue
                if not self.parser.is_category(fetched.text):
                    continue
                doc = html.fromstring(fetched.text)
                canonical = normalize_url(_first(doc.xpath(
                    "//link[contains(concat(' ',normalize-space(@rel),' '),' canonical ')]/@href"))
                    or fetched.final_url or source_url)
                breadcrumbs = [item for item in self.parser.parse_categories(doc)
                               if item.url and (normalize_url(item.url) or "").startswith(CATALOG_URL)]
                parent = next((normalize_url(item.url) for item in reversed(breadcrumbs)
                               if normalize_url(item.url) != canonical), candidates[source_url]["parent"])
                result.append({"category_name": _text(_first(doc.xpath("//h1"))) or candidates[source_url]["category_name"],
                               "category_url": canonical, "parent": None if canonical == CATALOG_URL else parent,
                               "discovery_sources": "navigation|category_page"})
                for link in doc.xpath("//a[contains(@class,'catalog-section-list-item-name')]"):
                    url = normalize_url(link.get("href"))
                    if not url or url in candidates:
                        continue
                    candidates[url] = {"category_name": clean_text(" ".join(link.itertext())),
                                       "category_url": url, "parent": canonical,
                                       "discovery_sources": "section_link"}
                    queue.append(url)
        return result

    async def category_urls(self, categories: list[dict]) -> tuple[dict[str, dict], dict[str, set[str]]]:
        """Fallback audit of every category page and its pagination."""
        product_evidence: dict[str, dict] = {}
        category_collections: dict[str, set[str]] = {}
        queue = [x["category_url"] for x in categories]
        seen_pages: set[str] = set()
        while queue:
            batch = []
            while queue and len(batch) < 12:
                page_url = queue.pop(0)
                if page_url not in seen_pages:
                    seen_pages.add(page_url)
                    batch.append(page_url)
            fetched_batch = await asyncio.gather(
                *(self.fetcher.get(url) for url in batch), return_exceptions=True)
            for page_url, fetched in zip(batch, fetched_batch):
                if isinstance(fetched, MaxRunDurationExceeded):
                    raise fetched
                if isinstance(fetched, Exception) or fetched.status_code != 200:
                    # The catalog root is known to reject ordinary GETs; its
                    # products are reached through the child categories.
                    if page_url != CATALOG_URL:
                        self._category_failure(page_url, fetched)
                    continue
                doc = html.fromstring(fetched.text)
                category_url = normalize_url(page_url.split("?", 1)[0])
                collection = next((v for p, v in COLLECTION_PATHS.items() if urlsplit(category_url).path.startswith(p)), None)
                for link in doc.xpath("//a[contains(concat(' ',normalize-space(@class),' '),' catalog-section-item-image-element ') or contains(concat(' ',normalize-space(@class),' '),' catalog-section-item-name-wrapper ')]"):
                    url = normalize_url(link.get("href"))
                    if not url or url == category_url:
                        continue
                    evidence = product_evidence.setdefault(url, {"category": category_url, "collections": set()})
                    if collection:
                        evidence["collections"].add(collection)
                    category_collections.setdefault(url, set()).add(category_url)
                for href in doc.xpath("//a[contains(@href,'PAGEN_')]/@href"):
                    normalized = normalize_url(href)
                    if normalized and normalized not in seen_pages:
                        queue.append(normalized)
        return product_evidence, category_collections

    async def discover_products(self) -> tuple[list[DiscoveryRecord], list[dict], list[str]]:
        maps, urls = await self.sitemap_urls()
        categories = await self.discover_categories(urls)
        category_products, _ = await self.category_urls(categories)
        now = datetime.now(timezone.utc).isoformat()
        records_by_url = {}
        for url in urls:
            collections = [value for prefix, value in COLLECTION_PATHS.items() if urlsplit(url).path.startswith(prefix)]
            records_by_url[url] = DiscoveryRecord(source_url=url, canonical_url=url, discovery_source="sitemap",
                                                    discovered_at=now, collections=collections)
        for url, evidence in category_products.items():
            if url in records_by_url:
                records_by_url[url].collections = list(dict.fromkeys(records_by_url[url].collections + sorted(evidence["collections"])))
            else:
                records_by_url[url] = DiscoveryRecord(source_url=url, canonical_url=url,
                    discovery_source="category_crawl", discovery_category=evidence["category"],
                    discovered_at=now, collections=sorted(evidence["collections"]))
        return list(records_by_url.values()), categories, maps
