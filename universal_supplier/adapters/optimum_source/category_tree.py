from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlsplit

from .constants import (
    CATALOG_URL,
    MERCHANDISING_NAMES,
    MERCHANDISING_PATHS,
)
from .normalize import normalize_url


def is_merchandising_category(url: str | None, name: str | None = None) -> bool:
    path = urlsplit(url or "").path
    return any(path.startswith(prefix) for prefix in MERCHANDISING_PATHS) or (
        (name or "").strip().casefold() in MERCHANDISING_NAMES
    )


def _product_categories(product) -> list[tuple[str, str]]:
    result = []
    for item in product.categories:
        url = normalize_url(item.url) if item.url else None
        if (not url or not url.startswith(CATALOG_URL) or
                is_merchandising_category(url, item.name)):
            continue
        result.append((url, item.name))
    return result


def build_category_tree(category_rows: list[dict], products: list) -> list[dict]:
    """Build a canonical, self-contained taxonomy from page and breadcrumb evidence."""
    names: dict[str, Counter] = defaultdict(Counter)
    parents: dict[str, Counter] = defaultdict(Counter)
    sources: dict[str, set[str]] = defaultdict(set)

    names[CATALOG_URL]["Каталог товаров"] += 1
    parents[CATALOG_URL][None] += 1
    sources[CATALOG_URL].add("root")

    for row in category_rows:
        url = normalize_url(row.get("category_url"))
        name = (row.get("category_name") or "").strip()
        if not url or not url.startswith(CATALOG_URL) or is_merchandising_category(url, name):
            continue
        parent = normalize_url(row.get("parent")) if row.get("parent") else None
        if url == CATALOG_URL:
            parent = None
        names[url][name or url] += 1
        parents[url][parent] += 1
        raw_sources = row.get("discovery_sources") or row.get("discovery_source") or "category_page"
        sources[url].update(x for x in str(raw_sources).split("|") if x)

    for product in products:
        categories = _product_categories(product)
        for index, (url, name) in enumerate(categories):
            parent = categories[index - 1][0] if index else None
            names[url][name or url] += 1
            parents[url][parent] += 1
            sources[url].add("breadcrumb")

    # Parent evidence is structural evidence too. It must never remain dangling.
    for child_url in list(parents):
        for parent_url in parents[child_url]:
            if parent_url and parent_url.startswith(CATALOG_URL):
                names[parent_url]  # create the node; its own evidence supplies its parent

    chosen_parent = {}
    for url in names:
        if url == CATALOG_URL:
            chosen_parent[url] = None
            continue
        options = parents.get(url, Counter())
        valid = Counter({p: count for p, count in options.items() if p and p != url})
        chosen_parent[url] = valid.most_common(1)[0][0] if valid else CATALOG_URL

    def depth(url: str, trail: set[str] | None = None) -> int:
        if url == CATALOG_URL:
            return 0
        trail = set(trail or ())
        if url in trail:
            raise ValueError(f"category parent cycle at {url}")
        trail.add(url)
        parent = chosen_parent.get(url)
        if parent not in chosen_parent:
            raise ValueError(f"dangling category parent {parent} referenced by {url}")
        return depth(parent, trail) + 1

    rows = []
    for url in names:
        name_counts = Counter({name: count for name, count in names[url].items() if name})
        rows.append({
            "category_name": name_counts.most_common(1)[0][0] if name_counts else url,
            "category_url": url,
            "parent": chosen_parent[url],
            "depth": depth(url),
            "discovery_sources": "|".join(sorted(sources[url])),
        })
    return sorted(rows, key=lambda row: (row["depth"], row["category_url"]))


def category_tree_qa(rows: list[dict], products: list, product_urls: set[str] | None = None) -> dict:
    by_url = {normalize_url(row["category_url"]): row for row in rows}
    product_urls = {normalize_url(url) for url in (product_urls or set()) if normalize_url(url)}
    dangling = sum(bool(row.get("parent")) and normalize_url(row["parent"]) not in by_url for row in rows)
    product_nodes = sum(normalize_url(row["category_url"]) in product_urls for row in rows)
    merchandising = sum(is_merchandising_category(row["category_url"], row["category_name"]) for row in rows)
    self_parents = sum(normalize_url(row["category_url"]) == normalize_url(row.get("parent")) for row in rows)

    cycles = 0
    for start in by_url:
        seen, current = set(), start
        while current in by_url and by_url[current].get("parent"):
            if current in seen:
                cycles += 1
                break
            seen.add(current)
            current = normalize_url(by_url[current]["parent"])

    depth_errors = 0
    for url, row in by_url.items():
        expected = 0 if not row.get("parent") else int(by_url.get(normalize_url(row["parent"]), {}).get("depth", -2)) + 1
        depth_errors += int(int(row["depth"]) != expected)

    breadcrumb_urls = {
        url for product in products for url, _ in _product_categories(product)
    }
    missing_breadcrumb = len(breadcrumb_urls - set(by_url))
    return {
        "CATEGORY_NODES": len(rows),
        "ROOT_NODES": sum(not row.get("parent") for row in rows),
        "DANGLING_PARENTS": dangling,
        "PRODUCT_URLS_IN_CATEGORY_TREE": product_nodes,
        "MERCHANDISING_CATEGORY_NODES": merchandising,
        "SELF_PARENT_NODES": self_parents,
        "CATEGORY_CYCLES": cycles,
        "DEPTH_ERRORS": depth_errors,
        "MISSING_BREADCRUMB_CATEGORY_NODES": missing_breadcrumb,
    }
