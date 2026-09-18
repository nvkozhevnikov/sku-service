from __future__ import annotations

import asyncio
import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import html

from universal_supplier.control_plane.rate_control import (
    DomainRatePolicy,
    MaxRunDurationExceeded,
)

from .constants import CATALOG_URL, COLLECTION_PATHS
from .category_tree import build_category_tree, category_tree_qa, is_merchandising_category
from .discovery import OptimumDiscovery
from .fetch import HttpFetcher
from .models import DiscoveryRecord
from .normalize import normalize_url
from .parser import OptimumProductParser, _class, _first, _text


AUDIT_FIELDS = [
    "source_url", "http_status", "h1", "canonical_url", "has_product_schema",
    "has_article", "article", "has_price", "price_raw", "has_availability",
    "availability_raw", "has_description", "has_properties", "has_gallery",
    "classification", "classification_reason",
]


class OptimumAdapter:
    """Read-only HTTP source adapter; it only writes local package artifacts."""

    def __init__(self, output_dir: str | Path | None = None,
                 concurrency: int | None = None, delay: float | None = None,
                 *, rate_policy: DomainRatePolicy | None = None, clock=None,
                 random_uniform=None, client=None):
        stamp = date.today().strftime("%Y%m%d")
        self.output_dir = Path(output_dir or f"OPTIMUM_SU_PARSER_ONLY_FINAL_{stamp}")
        self.fetcher = HttpFetcher(
            concurrency=concurrency,
            delay=delay,
            rate_policy=rate_policy,
            clock=clock,
            random_uniform=random_uniform,
            client=client,
        )
        self.rate_policy = self.fetcher.policy
        self.discovery = OptimumDiscovery(self.fetcher)
        self.parser = OptimumProductParser()

    async def discover_products(self):
        return await self.discovery.discover_products()

    async def discover_categories(self):
        return await self.discovery.discover_categories()

    async def fetch_product(self, url: str):
        return await self.fetcher.get(url)

    def parse_product(self, source: str, url: str, final_url: str | None = None, collections=None):
        return self.parser.parse_product(source, url, final_url, collections)

    @staticmethod
    def _canonical(doc, source_url: str, final_url: str) -> str:
        value = _first(doc.xpath("//link[contains(concat(' ',normalize-space(@rel),' '),' canonical ')]/@href"))
        return normalize_url(value or final_url or source_url) or source_url

    def _category_row(self, doc, source_url: str, final_url: str) -> dict:
        own_url = self._canonical(doc, source_url, final_url)
        breadcrumbs = self.parser.parse_categories(doc)
        parent = next((item.url for item in reversed(breadcrumbs) if item.url and item.url != own_url), None)
        if own_url == CATALOG_URL:
            parent = None
        return {"category_name": _text(_first(doc.xpath("//h1"))) or own_url,
                "category_url": own_url, "parent": parent,
                "depth": len([item for item in breadcrumbs if item.url and item.url != own_url])}

    def _audit_row(self, doc, source_url: str, final_url: str, classification: str,
                   reason: str, evidence: dict) -> dict:
        article = _text(_first(doc.xpath(f"//*[{_class('catalog-element-article-value')}]")))
        commercial = self.parser.parse_commercial(doc)
        return {
            "source_url": source_url, "http_status": 200,
            "h1": _text(_first(doc.xpath("//h1"))),
            "canonical_url": self._canonical(doc, source_url, final_url),
            "has_product_schema": evidence["has_product_schema"], "has_article": evidence["has_article"],
            "article": article, "has_price": evidence["has_price"],
            "price_raw": self.parser.parse_price_raw(doc), "has_availability": evidence["has_availability"],
            "availability_raw": commercial.availability_raw, "has_description": evidence["has_description"],
            "has_properties": evidence["has_properties"], "has_gallery": evidence["has_gallery"],
            "classification": classification, "classification_reason": reason,
        }

    async def _one(self, record: DiscoveryRecord):
        base = {"record": record, "status": None, "final_url": record.source_url,
                "classification": "FETCH_ERROR", "reason": "fetch did not complete"}
        try:
            fetched = await self.fetch_product(record.source_url)
        except MaxRunDurationExceeded:
            # Deadline is a crawl-level terminal condition, not an ordinary
            # per-URL fetch error.  Let the orchestrating batch cancel work
            # which has not completed and preserve only finished results.
            raise
        except Exception as exc:
            return base | {"error_type": type(exc).__name__, "error": str(exc)}
        base.update(status=fetched.status_code, final_url=fetched.final_url,
                    attempt_count=fetched.attempt_count)
        if fetched.status_code == 404:
            return base | {"classification": "STALE_404", "reason": "HTTP 404 on fresh crawl"}
        if fetched.status_code != 200:
            return base | {"classification": "FETCH_ERROR", "reason": f"HTTP {fetched.status_code}",
                           "error_type": "HTTPStatus", "error": f"HTTP {fetched.status_code}"}
        try:
            doc = html.fromstring(fetched.text)
            evidence = self.parser.page_evidence(fetched.text)
            if evidence["is_product"]:
                product = self.parse_product(fetched.text, record.source_url, fetched.final_url, record.collections)
                reason = f"main product component with H1 and {evidence['support_score']} product-specific signals"
                return base | {"classification": "PRODUCT", "reason": reason, "product": product,
                               "category": None, "audit": self._audit_row(
                                   doc, record.source_url, fetched.final_url, "LIVE_PRODUCT", reason, evidence)}
            if self.parser.is_category(fetched.text):
                reason = (f"taxonomy/listing markup ({evidence['category_cards']} product cards, "
                          f"{evidence['category_sections']} section links) without main product component")
                return base | {"classification": "CATEGORY", "reason": reason, "product": None,
                               "category": self._category_row(doc, record.source_url, fetched.final_url),
                               "audit": self._audit_row(
                                   doc, record.source_url, fetched.final_url, "CATEGORY", reason, evidence)}
            reason = "HTTP 200 but insufficient product-specific or taxonomy evidence"
            return base | {"classification": "OTHER", "reason": reason, "product": None,
                           "category": None, "audit": self._audit_row(
                               doc, record.source_url, fetched.final_url, "OTHER", reason, evidence)}
        except Exception as exc:
            return base | {"classification": "FETCH_ERROR", "reason": "parse failure",
                           "error_type": type(exc).__name__, "error": str(exc)}

    @staticmethod
    def _deduplicate(product_results: list[dict]):
        groups = defaultdict(list)
        for result in product_results:
            identity = result["product"].identity
            groups[(identity.external_id_type, identity.external_id)].append(result)
        unique, aliases, duplicate_rows = [], [], []
        for key, items in groups.items():
            items.sort(key=lambda result: (
                result["product"].identity.source_url != result["product"].identity.canonical_url,
                -len(result["product"].properties), -len(result["product"].images)))
            winner = items[0]
            unique.append(winner["product"])
            for alias in items[1:]:
                product = alias["product"]
                winner["product"].collections = list(dict.fromkeys(winner["product"].collections + product.collections))
                reason = f"same {key[0]} on multiple live product URLs"
                aliases.append({"primary_product_url": winner["product"].identity.canonical_url,
                                "alias_url": product.identity.source_url, "external_id": key[1], "reason": reason})
                alias["classification"], alias["reason"] = "PRODUCT_ALIAS", reason
                alias["audit"]["classification"], alias["audit"]["classification_reason"] = "PRODUCT_ALIAS", reason
                duplicate_rows.append({"duplicate_type": key[0], "duplicate_value": key[1],
                                       "kept_url": winner["product"].identity.canonical_url,
                                       "duplicate_url": product.identity.source_url,
                                       "action": "recorded_as_product_alias"})
        return unique, aliases, duplicate_rows

    @staticmethod
    def _write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
        fields = fields or (list(rows[0]) if rows else ["status"])
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _is_merchandising(url: str) -> bool:
        return is_merchandising_category(url)

    def _export(self, products, discovery, results, aliases, duplicate_rows, sitemap_maps,
                discovered_categories):
        out = self.output_dir
        product_results = [r for r in results if r["classification"] in {"PRODUCT", "PRODUCT_ALIAS"}]
        http_200 = [r for r in results if r["status"] == 200]
        stale = [r for r in results if r["classification"] == "STALE_404"]
        fetch_errors = [r for r in results if r["classification"] == "FETCH_ERROR"]
        categories = [r for r in results if r["classification"] == "CATEGORY"]
        others = [r for r in results if r["classification"] == "OTHER"]

        with (out / "OPTIMUM_PRODUCTS_CANONICAL.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for product in products:
                fh.write(product.model_dump_json(exclude_none=False) + "\n")
        qa = []
        for product in products:
            i, c = product.identity, product.commercial
            qa.append({"external_id": i.external_id, "external_id_type": i.external_id_type, "sku": i.sku,
                       "name": i.name, "canonical_url": i.canonical_url,
                       "primary_category": product.categories[-1].name if product.categories else "",
                       "price": c.price, "source_price_text": product.raw_data.get("source_price_text"),
                       "currency": c.currency, "availability": c.availability_normalized,
                       "availability_raw": c.availability_raw, "quantity": c.quantity,
                       "properties_count": len(product.properties), "images_count": len(product.images),
                       "documents_count": len(product.documents),
                       "accessories_count": sum(x.relation_type == "accessory" for x in product.related_products),
                       "parse_status": product.parse_status})
        self._write_csv(out / "OPTIMUM_PRODUCTS_QA.csv", qa)

        result_by_url = {r["record"].source_url: r for r in results}
        disc_rows = []
        for record in discovery:
            result = result_by_url[record.source_url]
            disc_rows.append(record.model_dump(mode="json") | {
                "collections": "|".join(record.collections), "http_status": result["status"],
                "classification": result["classification"], "classification_reason": result["reason"]})
        self._write_csv(out / "OPTIMUM_PRODUCT_DISCOVERY.csv", disc_rows)

        product_urls = {r["record"].source_url for r in product_results}
        product_urls.update(p.identity.source_url for p in products)
        product_urls.update(p.identity.canonical_url for p in products)
        category_evidence, seen_categories = list(discovered_categories), set()
        for result in categories:
            row = result["category"]
            if (not row or self._is_merchandising(row["category_url"]) or
                    row["category_url"] in seen_categories or row["category_url"] in product_urls):
                continue
            seen_categories.add(row["category_url"])
            category_evidence.append(row | {"discovery_sources": "sitemap|category_page"})
        category_rows = build_category_tree(category_evidence, products)
        self._write_csv(out / "OPTIMUM_CATEGORY_TREE.csv", category_rows,
                        ["category_name", "category_url", "parent", "depth", "discovery_sources"])
        self._write_csv(out / "OPTIMUM_PRODUCT_ALIASES.csv", aliases,
                        ["primary_product_url", "alias_url", "external_id", "reason"])
        self._write_csv(out / "OPTIMUM_UNCLASSIFIED_URL_AUDIT.csv",
                        [r["audit"] for r in http_200 if r.get("audit")], AUDIT_FIELDS)
        self._write_csv(out / "OPTIMUM_DUPLICATE_AUDIT.csv", duplicate_rows,
                        ["duplicate_type", "duplicate_value", "kept_url", "duplicate_url", "action"])
        error_rows = [{"source_url": r["record"].source_url, "http_status": r["status"],
                       "error_type": r.get("error_type", "HTTP404"), "classification": r["classification"],
                       "error": r.get("error", r["reason"])} for r in stale + fetch_errors]
        self._write_csv(out / "OPTIMUM_ERRORS.csv", error_rows,
                        ["source_url", "http_status", "error_type", "classification", "error"])

        total = len(products)
        metrics = {
            "EXTERNAL_ID_PRESENT": sum(bool(p.identity.external_id) for p in products),
            "SKU_PRESENT": sum(bool(p.identity.sku) for p in products),
            "INTERNAL_ID_PRESENT": sum(p.identity.external_id_type == "internal_product_id" for p in products),
            "CANONICAL_ID_FALLBACK": sum(p.identity.external_id_type == "canonical_url" for p in products),
            "NAME_PRESENT": sum(bool(p.identity.name) for p in products),
            "BRAND_PRESENT": sum(bool(p.identity.brand_raw) for p in products),
            "MANUFACTURER_PRESENT": sum(bool(p.identity.manufacturer_raw) for p in products),
            "PRICE_PRESENT": sum(bool(p.commercial.price) for p in products),
            "AVAILABILITY_PRESENT": sum(bool(p.commercial.availability_raw) for p in products),
            "DESCRIPTION_PRESENT": sum(bool(p.description.text) for p in products),
            "PROPERTIES_PRESENT": sum(bool(p.properties) for p in products),
            "IMAGES_PRESENT": sum(bool(p.images) for p in products),
            "DOCUMENTS_PRESENT": sum(bool(p.documents) for p in products),
            "PACKAGE_CONTENTS_PRESENT": sum(bool(p.package_contents) for p in products),
            "ACCESSORIES_PRESENT": sum(any(x.relation_type == "accessory" for x in p.related_products) for p in products),
            "CANONICAL_PRESENT": sum(bool(p.identity.canonical_url) for p in products),
        }
        self._write_csv(out / "OPTIMUM_FIELD_COVERAGE.csv", [
            {"field": k, "present": v, "missing": total - v,
             "coverage_percent": f"{(v / total * 100 if total else 0):.2f}"} for k, v in metrics.items()])

        external_ids = [p.identity.external_id for p in products if p.identity.external_id]
        skus = [p.identity.sku for p in products if p.identity.sku]
        duplicate_external_ids = sum(v - 1 for v in Counter(external_ids).values() if v > 1)
        duplicate_skus = sum(v - 1 for v in Counter(skus).values() if v > 1)
        discovery_ok = len(discovery) == len(http_200) + len(stale) + len(fetch_errors)
        page_ok = len(http_200) == len(product_results) + len(categories) + len(others)
        qw = {"qw125": False, "qw100": False}
        for result in product_results:
            for key in qw:
                if key in result["record"].source_url.lower():
                    qw[key] = True
        tree_qa = category_tree_qa(category_rows, products, product_urls)
        category_ok = (
            tree_qa["ROOT_NODES"] == 1 and
            all(tree_qa[key] == 0 for key in (
                "DANGLING_PARENTS", "PRODUCT_URLS_IN_CATEGORY_TREE",
                "MERCHANDISING_CATEGORY_NODES", "SELF_PARENT_NODES",
                "CATEGORY_CYCLES", "DEPTH_ERRORS",
                "MISSING_BREADCRUMB_CATEGORY_NODES"))
        )
        identity_ok = metrics["EXTERNAL_ID_PRESENT"] == total and all(
            p.identity.external_id_type in {"internal_product_id", "canonical_url"} for p in products)
        detection_ok = all(qw.values())
        parser_ok = total > 0 and not fetch_errors and all(p.parse_status == "OK" for p in products)
        overall = all((discovery_ok, page_ok, category_ok, identity_ok, detection_ok, parser_ok,
                       duplicate_external_ids == 0, duplicate_skus == 0))
        summary = {
            "SUPPLIER": "Optimum", "SUPPLIER_CODE": "optimum", "DISCOVERED_URLS": len(discovery),
            "FETCHED_URLS": len(results), "HTTP_200_URLS": len(http_200), "STALE_404_URLS": len(stale),
            "FETCH_ERROR_URLS": len(fetch_errors), "PRODUCT_PAGE_URLS": len(product_results),
            "CATEGORY_PAGE_URLS": len(categories), "OTHER_URLS": len(others),
            "PRODUCT_ALIAS_URLS": len(aliases), "PRODUCT_ALIAS_SUBSET_OF_PRODUCT_PAGE_URLS": "YES",
            "UNIQUE_PRODUCTS": total, "PRODUCTS_TOTAL": total,
            "SKU_PRESENT": metrics["SKU_PRESENT"], "SKU_MISSING": total - metrics["SKU_PRESENT"],
            "INTERNAL_ID_PRESENT": metrics["INTERNAL_ID_PRESENT"],
            "CANONICAL_ID_FALLBACK": metrics["CANONICAL_ID_FALLBACK"],
            "EXTERNAL_ID_PRESENT": metrics["EXTERNAL_ID_PRESENT"],
            "DUPLICATE_EXTERNAL_IDS": duplicate_external_ids, "DUPLICATE_SKUS": duplicate_skus,
            "BRAND_PRESENT": metrics["BRAND_PRESENT"], "BRAND_MISSING": total - metrics["BRAND_PRESENT"],
            "MANUFACTURER_PRESENT": metrics["MANUFACTURER_PRESENT"],
            "MANUFACTURER_MISSING": total - metrics["MANUFACTURER_PRESENT"],
            "PRICE_PRESENT": metrics["PRICE_PRESENT"], "AVAILABILITY_PRESENT": metrics["AVAILABILITY_PRESENT"],
            "PLAYWRIGHT_REQUIRED": "NO", "PROXY_USED": "NO", "POSTGRES_WRITES": 0,
            "STERBRUST_WRITES": 0, "XML_CREATED": "NO",
            **tree_qa,
            "DISCOVERY_RECONCILIATION_QA": "PASS" if discovery_ok and page_ok else "FAIL",
            "PRODUCT_DETECTION_QA": "PASS" if detection_ok else "FAIL",
            "CATEGORY_TREE_QA": "PASS" if category_ok else "FAIL",
            "IDENTITY_QA": "PASS" if identity_ok and duplicate_external_ids == 0 else "FAIL",
            "PARSER_QA": "PASS" if parser_ok else "FAIL", "OVERALL_QA": "PASS" if overall else "FAIL",
        }
        (out / "PARSER_SUMMARY.txt").write_text(
            "\n".join(f"{k} = {v}" for k, v in summary.items()) + "\n", encoding="utf-8")
        report = f"""# Optimum source adapter report

Crawl date: {date.today().isoformat()}

- Discovery: sitemap index `{len(sitemap_maps)}` child maps plus catalog HTML fallback.
- Classification: main product component + H1 + multiple product-specific signals; SKU is optional.
- Missing-SKU identity: proven Bitrix element ID, otherwise canonical URL; never name/model.
- Aliases: duplicate live identity is one unique product plus an explicit URL alias.
- Category tree: navigation + category pages + section links + product breadcrumbs + sitemap evidence.
- Tree integrity: {tree_qa['CATEGORY_NODES']} nodes, {tree_qa['ROOT_NODES']} root, dangling parents {tree_qa['DANGLING_PARENTS']}, cycles {tree_qa['CATEGORY_CYCLES']}, missing breadcrumb nodes {tree_qa['MISSING_BREADCRUMB_CATEGORY_NODES']}.
- Fetch: ordinary read-only HTTP GET; all discovered URLs were freshly requested.
- Safety: proxy NO; PostgreSQL writes 0; Sterbrust writes 0; XML NO.
- Reconciliation: `{len(discovery)} = {len(http_200)} + {len(stale)} + {len(fetch_errors)}`; HTTP 200 `{len(http_200)} = {len(product_results)} + {len(categories)} + {len(others)}`.
- QA result: {summary['OVERALL_QA']}.
"""
        (out / "PARSER_REPORT.md").write_text(report, encoding="utf-8")
        return summary

    def _package_sources(self):
        source_dir = self.output_dir / "parser_source" / "optimum_parser"
        source_dir.mkdir(parents=True, exist_ok=True)
        for source in Path(__file__).parent.glob("*.py"):
            (source_dir / source.name).write_bytes(source.read_bytes())
        tests_target = self.output_dir / "tests"
        tests_target.mkdir(parents=True, exist_ok=True)
        test_source = Path("tests/test_optimum_parser.py")
        if test_source.exists():
            (tests_target / test_source.name).write_bytes(test_source.read_bytes())
        fixture_source = Path("tests/fixtures")
        if fixture_source.exists():
            fixture_target = tests_target / "fixtures"
            fixture_target.mkdir(parents=True, exist_ok=True)
            for source in fixture_source.glob("*.html"):
                (fixture_target / source.name).write_bytes(source.read_bytes())
        (self.output_dir / "pyproject.toml").write_text("""[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "optimum-su-parser"
version = "1.2.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2,<3", "lxml>=5,<7", "httpx>=0.27,<1"]

[project.optional-dependencies]
test = ["pytest>=8,<10"]

[project.scripts]
optimum-parser = "optimum_parser.cli:main"

[tool.setuptools]
package-dir = {"" = "parser_source"}

[tool.setuptools.packages.find]
where = ["parser_source"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""", encoding="utf-8")

    async def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            discovery, discovered_categories, sitemap_maps = await self.discover_products()
            results = []
            for start in range(0, len(discovery), 24):
                results.extend(await asyncio.gather(*(self._one(record) for record in discovery[start:start + 24])))
            products, aliases, duplicate_rows = self._deduplicate(
                [r for r in results if r["classification"] == "PRODUCT"])
            summary = self._export(products, discovery, results, aliases, duplicate_rows,
                                   sitemap_maps, discovered_categories)
            self._package_sources()
            zip_path = self.output_dir.parent / f"OPTIMUM_SU_PARSER_ONLY_FINAL_{date.today():%Y%m%d}.zip"
            with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
                for path in self.output_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, Path(self.output_dir.name) / path.relative_to(self.output_dir))
            return summary, zip_path
        finally:
            await self.fetcher.close()
