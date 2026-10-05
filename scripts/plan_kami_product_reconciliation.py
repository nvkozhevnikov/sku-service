"""Pinned offline URL reconciliation; never promotes sitemap to product proof."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from universal_supplier.adapters.kami import canonical_source_url

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'


def main():
    path = BASE / 'CATALOG_DISCOVERY/CHECKPOINT.json'
    raw = path.read_bytes()
    state = json.loads(raw)
    if state['status'] != 'LISTING_TRAVERSAL_COMPLETE_PRODUCT_RECONCILIATION_PENDING' or state['queue']:
        raise RuntimeError('Listing traversal must finish before reconciliation plan')
    invpath = BASE / 'SITEMAP_SCOPE_INVENTORY.json'
    inv = json.loads(invpath.read_bytes())
    # Completed pagination proves its category URL, never a query-bearing product.
    visited = {canonical_source_url(urlsplit(u)._replace(query='', fragment='').geturl())
               for u in state['completed'] if urlsplit(u).path.startswith('/catalog/')}
    listing = {canonical_source_url(u) for u in state['products']}
    sitemap = {canonical_source_url(u) for u in inv['candidate_catalog_urls']}
    remaining = sorted((sitemap | listing) - visited)
    result = {
        'status': 'PRODUCT_RECONCILIATION_REQUIRED_NOT_FULL_DISCOVERY_PASS',
        'scope': 'www.stanki.ru equipment/machines; zip.stanki.ru excluded by operator',
        'input_sha256': {'listing_checkpoint': hashlib.sha256(raw).hexdigest(),
                         'sitemap_inventory': hashlib.sha256(invpath.read_bytes()).hexdigest()},
        'completed_listing_pages': len(state['completed']),
        'listing_urls': len(listing), 'sitemap_catalog_urls': len(sitemap),
        'listing_not_in_sitemap': sorted(listing - sitemap),
        'candidate_detail_urls': remaining,
        'candidate_count': len(remaining),
        'identity_contract': 'variant internal ID > parent site internal ID > canonical URL SHA256',
        'unverified_candidates_not_product_count': True,
        'known_stale_listing_urls': state['excluded'],
        'no_automatic_deactivation': True,
    }
    out = BASE / 'PRODUCT_RECONCILIATION_PLAN.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
