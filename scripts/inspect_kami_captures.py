"""Offline source inventory, not a product completeness verdict."""
import collections
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup

OUT = Path(__file__).resolve().parents[1] / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/FULL_SITEMAPS'


def main():
    ledger = json.loads((OUT / 'HTTP_AUDIT.json').read_text(encoding='utf-8'))
    locations, pages = [], []
    for record in ledger:
        if record.get('status') != 200:
            continue
        raw = (OUT / record['capture']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        if 'xml' in record['content_type']:
            root = ET.fromstring(raw)
            locations.extend(e.text.strip() for e in root.findall('{*}url/{*}loc') if e.text)
        elif 'html' in record['content_type']:
            html = raw.decode('cp1251' if 'windows-1251' in record['content_type'] else 'utf-8')
            soup = BeautifulSoup(html, 'html.parser')
            cards = soup.select('.productCard')
            pages.append({'url': record['url'], 'h1': [h.get_text(' ', strip=True) for h in soup.select('h1')],
                          'cards': len(cards), 'card_examples': [str(c)[:8500] for c in cards[:2]],
                          'catalog_links': sorted(set(urljoin(record['url'], a['href']) for a in soup.select('a[href]') if a['href'].startswith('/catalog/'))),
                          'pagination': [dict(e.attrs) for e in soup.select('[data-pagination-more], [data-pagination-button]')],
                          'title': soup.title.get_text() if soup.title else ''})
    unique = sorted(set(locations))
    catalog = [u for u in unique if urlsplit(u).path.startswith('/catalog/')]
    result = {'status': 'DISCOVERY_SCOPE_ASSESSMENT_NOT_COMPLETE', 'raw_locations': len(locations),
              'unique_locations': len(unique), 'duplicates': len(locations)-len(unique),
              'hosts': dict(collections.Counter(urlsplit(u).hostname for u in unique)),
              'catalog_urls': len(catalog),
              'catalog_path_depth': dict(collections.Counter(len(urlsplit(u).path.strip('/').split('/')) for u in catalog)),
              'pages': pages, 'candidate_catalog_urls': catalog}
    (OUT.parent / 'SITEMAP_SCOPE_INVENTORY.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in {'pages', 'candidate_catalog_urls'}}, ensure_ascii=False))
    for page in pages:
        print(json.dumps({k: v for k, v in page.items() if k not in {'catalog_links', 'card_examples'}}, ensure_ascii=False))
        for example in page['card_examples']:
            print(example)


if __name__ == '__main__':
    main()
