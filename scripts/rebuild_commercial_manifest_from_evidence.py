"""Rebuild a candidate manifest from already-sanitised catalog evidence only."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.commercial_collection import CandidateManifest, write_candidate_manifest
from universal_supplier.commercial_discovery import discover_catalog_page


FALLBACK_CATALOGS = {
    "intervesp": "https://intervesp.ru/catalog/konsolnye-poluavtomaticheskie-lentochnopilnye-stanki/",
    "beka_mak": "https://beka-mak.su/catalog/poluavtomaticheskie-lentochnopilnye-stanki/",
}
EVIDENCE_HOSTS = {"intervesp": "intervesp.ru", "beka_mak": "beka-mak.su"}
_CANONICAL_RE = re.compile(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)', re.I)


def manifest_from_evidence(root: Path) -> CandidateManifest:
    sites = {}
    for site, fallback in FALLBACK_CATALOGS.items():
        found = {}
        for path in sorted((root / "catalog" / EVIDENCE_HOSTS[site]).glob("*.html")):
            html = path.read_text(encoding="utf-8", errors="replace")
            match = _CANONICAL_RE.search(html)
            page = match.group(1) if match else fallback
            for product in discover_catalog_page(site, page, html).products:
                found[product.product_url] = product
        sites[site] = tuple(sorted(found.values(), key=lambda item: (item.expected_model, item.product_url)))
    return CandidateManifest(sites)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild commercial manifest from existing sanitized evidence.")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = manifest_from_evidence(args.evidence_dir)
    write_candidate_manifest(manifest, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
