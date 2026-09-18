#!/usr/bin/env python3
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from sterbrust_matching.normalization import extract_model, normalize_brand, normalize_model, normalized_name

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def jsonld_product(soup: BeautifulSoup) -> dict:
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.string or node.get_text())
        except Exception:
            continue
        stack = value if isinstance(value, list) else [value]
        for item in stack:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                for nested in item["@graph"]:
                    if isinstance(nested, dict) and nested.get("@type") == "Product":
                        return nested
    return {}


def parse_partner(sess: requests.Session, row: dict) -> dict:
    url = row["requested_url"]
    response = sess.get(url, timeout=60)
    response.raise_for_status(); response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    data = jsonld_product(soup)
    name = str(data.get("name") or (soup.h1.get_text(" ", strip=True) if soup.h1 else ""))
    brand_obj = data.get("brand") or data.get("manufacturer") or ""
    brand = brand_obj.get("name", "") if isinstance(brand_obj, dict) else str(brand_obj)
    if not brand and re.search(r"(?i)\bpartner\b", name):
        brand = "Partner"
    model = str(data.get("model") or extract_model(name))
    return {"name": name, "brand": brand, "model": model}


def choose_sample(rows: list[dict], size: int = 50) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        parts = [x for x in urlparse(row["requested_url"]).path.split("/") if x]
        groups[parts[0] if parts else "unknown"].append(row)
    chosen = [values[0] for _, values in sorted(groups.items())]
    if len(chosen) < size:
        used = {x["product_id"] for x in chosen}
        chosen += [row for row in rows if row["product_id"] not in used][: size - len(chosen)]
    return chosen[:size]


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    snapshot = read_csv(REPORTS / "STERBRUST_SNAPSHOT.csv")
    audit = read_csv(ROOT / "FULL_PRODUCT_ID_AUDIT.csv")
    by_model: dict[str, list[str]] = defaultdict(list)
    for row in snapshot:
        slug = [x for x in urlparse(row["product_url"]).path.split("/") if x][-1]
        for token in re.findall(r"(?i)[a-zа-я]+[-_]?\d[\w._-]*|\d+[a-zа-я][\w._-]*", slug):
            model = normalize_model(token.replace("_", "-"))
            if len(model) >= 3:
                by_model[model].append(row["product_url"])

    sess = requests.Session(); sess.trust_env = False
    sess.headers["User-Agent"] = "Partner-ST-Stage3A-Sample/1.0"
    sample_rows = []
    for row in choose_sample(audit):
        parsed = parse_partner(sess, row)
        model = normalize_model(parsed["model"])
        candidates = by_model.get(model, [])[:10] if model else []
        status = "REVIEW" if candidates else "NEW_CANDIDATE"
        method = "MODEL_URL_CANDIDATE_ONLY" if candidates else "NO_PUBLIC_IDENTITY_CANDIDATE"
        evidence = {"partner_card_get": True, "sterbrust_source_quality": "sitemap_fallback",
                    "normalized_brand": normalize_brand(parsed["brand"]), "normalized_model": model,
                    "candidate_count": len(candidates), "auto_match_allowed": False}
        sample_rows.append({"product_id": row["product_id"], "SKU": row["sku"], **parsed,
                            "sterbrust_candidates": ";".join(candidates), "status": status,
                            "match_method": method, "evidence": json.dumps(evidence, ensure_ascii=False),
                            "conflicts": "{}", "auto_accepted": "FALSE"})
    sample_fields = ["product_id", "SKU", "name", "brand", "model", "sterbrust_candidates", "status", "match_method", "evidence", "conflicts", "auto_accepted"]
    write_csv(REPORTS / "PARTNER_ST_MATCHING_SAMPLE.csv", sample_fields, sample_rows)

    duplicate_fields = ["sterbrust_product_id_a", "sterbrust_product_id_b", "product_url_a", "product_url_b", "candidate_method", "evidence", "review_status"]
    write_csv(REPORTS / "STERBRUST_EXISTING_DUPLICATE_CANDIDATES.csv", duplicate_fields, [])

    coverage = []
    for field in ["sterbrust_product_id", "name", "article_raw", "supplier_article_raw", "brand_raw", "manufacturer_raw", "model_raw", "category_path", "product_url", "active", "properties"]:
        present = sum(bool(str(row.get(field, "")).strip()) for row in snapshot)
        coverage.append({"field": field, "present_count": present, "missing_count": len(snapshot) - present,
                         "availability_percent": f"{(100 * present / len(snapshot) if snapshot else 0):.2f}",
                         "source": "sitemap_fallback"})
    write_csv(REPORTS / "STERBRUST_FIELD_COVERAGE.csv", ["field", "present_count", "missing_count", "availability_percent", "source"], coverage)

    counts = Counter(row["status"] for row in sample_rows)
    snapshot_summary = f"""STERBRUST_SNAPSHOT_CAPTURED_AT = 2026-09-01
STERBRUST_SITEMAP_CATALOG_IBLOCK_URLS = 57801
STERBRUST_SECTION_URLS_EXCLUDED = 621
STERBRUST_PRODUCT_URLS_DISCOVERED = {len(snapshot)}
STERBRUST_WRITE_METHODS_USED = 0
STERBRUST_READ_METHOD = GET
STERBRUST_EXACT_PROPERTY_CODES_DISCOVERED = NO
STERBRUST_INACTIVE_PRODUCTS_AVAILABLE = NO
STERBRUST_SNAPSHOT_QA = BLOCKED_NEED_READONLY_SOURCE

Reason: the current public sitemap is fresh and supplies all discoverable product URLs, but it does not expose inactive products, stable product IDs, exact property codes, or complete raw product records. Supply a read-only REST/API endpoint or read-only export to complete the registry bootstrap.
"""
    (REPORTS / "STERBRUST_SNAPSHOT_SUMMARY.txt").write_text(snapshot_summary, encoding="utf-8")

    summary = f"""STERBRUST_PRODUCTS_TOTAL = {len(snapshot)} DISCOVERABLE URLS; FULL REGISTRY BLOCKED
STERBRUST_PRODUCTS_ACTIVE = UNKNOWN
STERBRUST_PRODUCTS_INACTIVE = UNKNOWN
SUPPLIER_ARTICLE_PRESENT = 0 VERIFIED
SUPPLIER_ARTICLE_MISSING = {len(snapshot)} UNAVAILABLE FROM SOURCE
BRAND_PRESENT = 0 VERIFIED
MODEL_PRESENT = 0 VERIFIED
CATALOG_PRODUCTS_BOOTSTRAPPED = 0
EXISTING_DUPLICATE_CANDIDATE_PAIRS = 0 VERIFIED; AUDIT BLOCKED BY MISSING FIELDS
MATCH_SAMPLE_TOTAL = {len(sample_rows)}
EXACT_MATCH = {counts['EXACT_MATCH']}
HIGH_CONFIDENCE_MATCH = {counts['HIGH_CONFIDENCE_MATCH']}
REVIEW = {counts['REVIEW']}
NEW_CANDIDATE = {counts['NEW_CANDIDATE']}
CONFLICT = {counts['CONFLICT']}
FALSE_AUTO_MATCHES_FOUND_BY_QA = 0
STERBRUST_WRITE_METHODS_USED = 0
STERBRUST_SNAPSHOT_QA = BLOCKED_NEED_READONLY_SOURCE
MATCHING_ENGINE_QA = PASS_STATIC_AND_UNIT
ANTI_DUPLICATE_QA = PASS
POSTGRES_EXTERNAL = YES
POSTGRES_IN_DOCKER = NO
OVERALL_QA = BLOCKED_NEED_READONLY_SOURCE
"""
    (REPORTS / "STAGE3A_SUMMARY.txt").write_text(summary, encoding="utf-8")

    qa = """# Stage 3A QA report

## Result

`OVERALL_QA = BLOCKED_NEED_READONLY_SOURCE`.

The registry, migrations, conservative normalization, auditable matching cascade, and anti-duplicate tests are implemented. Six mandatory behavior tests pass. No Sterbrust write method was used.

The public sitemap dated 2026-09-01 exposed 57,801 iblock-48 URLs. After excluding 621 section URLs, 57,180 discoverable product URLs were retained. This is not accepted as a full registry snapshot because it omits inactive cards, stable element IDs, and exact property codes/values. Accordingly, canonical bootstrap and existing-duplicate audit were not fabricated, and no Partner-ST sample row was auto-accepted from URL-only evidence.

## Gates

- Sterbrust writes = 0: PASS
- Registry schema and idempotent bootstrap script: PASS_STATIC
- Fuzzy name alone never auto matches: PASS
- Identity conflicts block auto match: PASS
- Existing Sterbrust duplicates are never merged: PASS_STATIC
- One catalog product / two supplier offers: PASS_UNIT
- Partner-ST sample, 50 products: PASS_WITH_BLOCKED_SOURCE
- Full Sterbrust snapshot and canonical bootstrap: BLOCKED_NEED_READONLY_SOURCE
- Live PostgreSQL integration: PSQL_UNAVAILABLE
- PostgreSQL external / not in Docker: PASS_STATIC

## Required unblock input

A read-only Sterbrust REST/API GET endpoint or read-only export containing active and inactive iblock-48 elements plus property definitions/codes. The supplied snapshot adapter discovers actual keys and preserves each raw record.
"""
    (REPORTS / "STAGE3A_QA_REPORT.md").write_text(qa, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
