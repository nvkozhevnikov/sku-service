"""Freeze independently scoped discovery from saved audited sitemap bytes."""
from pathlib import Path
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_ltt_uss_sources import slug_for
from universal_supplier.ltt_uss_capture import is_product_candidate, safe_source_url

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'


def freeze():
    audit = json.loads((OUT / 'AUDIT/SOURCE_AUDIT_SUMMARY.json').read_text('utf-8'))
    target = OUT / 'MANIFESTS'
    target.mkdir(exist_ok=True)
    receipts = {}
    for supplier in ('ltt_msk', 'ltt_sib', 'ltt_yug', 'uss_stanko'):
        urls = {}
        excluded = {}
        for entry in audit['ledger']:
            if entry['source'] != supplier or not entry['label'].startswith('sitemap:'):
                continue
            path = OUT / 'AUDIT/RAW' / (slug_for(entry['url']) + '.xml')
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
            root = ET.fromstring(raw)
            if root.tag.rsplit('}', 1)[-1] != 'urlset':
                continue
            for node in root.iter():
                if node.tag.rsplit('}', 1)[-1] == 'loc' and node.text:
                    url = node.text.strip()
                    safe_source_url(supplier, url)
                    ok, reason = is_product_candidate(supplier, url)
                    if ok:
                        urls.setdefault(url, []).append(dict(sitemap=entry['url'], sha256=entry['sha256'], reason=reason))
                    elif reason.startswith('NON_PRODUCT_CATEGORY_PAGE:'):
                        excluded.setdefault(url, []).append(dict(sitemap=entry['url'], sha256=entry['sha256'], reason=reason))
        twins = {url: url[:-9] + '.html' for url in urls if url.endswith('-rub.html') and url[:-9] + '.html' in urls} if supplier == 'ltt_yug' else {}
        rows = [dict(supplier=supplier, canonical_url=url, source_internal_id=None, category=urlparse(url).path.rsplit('/', 1)[0],
                     title_hint=None, discovery_path=urls[url],
                     status='DISCOVERED_NOT_FETCHED',
                     similar_slug_url=twins.get(url)) for url in sorted(urls)]
        payload = dict(format='ltt-uss-discovery-v2', supplier=supplier, candidates=rows,
                       identity_policy='stable site_internal_id/data-product; suffix/model/title are not identity; distinct IDs KEEP BOTH; aliases require explicit source evidence',
                       counts=dict(discovered=len(rows), manifest_total=len(rows), suffix_exclusions=0, slug_similarity_pairs=len(twins)))
        if excluded:
            payload['non_product_discovery'] = [dict(canonical_url=url, status='NON_PRODUCT_CATEGORY_PAGE',
                provenance='OPERATOR-CONFIRMED', decision='decisions/2026-10-14_LTT_YUG_ID1_EMPTY_CATEGORY.md',
                discovery_path=excluded[url]) for url in sorted(excluded)]
            payload['counts'].update(discovered=len(rows)+len(excluded), non_product_category_page_count=len(excluded))
        body = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
        (target / (supplier + '.json')).write_bytes(body)
        receipts[supplier] = dict(sha256=hashlib.sha256(body).hexdigest(), **payload['counts'],
                                 status='MANIFEST_FROZEN')
    (target / 'SHA256.json').write_text(json.dumps(receipts, indent=2) + '\n', 'utf-8')
    return receipts


if __name__ == '__main__':
    print(json.dumps(freeze()))
