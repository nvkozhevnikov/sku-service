"""Offline detail structure inspection; no network or legacy imports."""
import json
from pathlib import Path
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1] / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'


def main():
    ledger = json.loads((BASE / 'CONTROLLED_SAMPLE/HTTP_AUDIT.json').read_text(encoding='utf-8'))
    inventory = json.loads((BASE / 'SITEMAP_SCOPE_INVENTORY.json').read_text(encoding='utf-8'))
    sitemap = set(inventory['candidate_catalog_urls'])
    coverage = []
    for rec in ledger:
        if 'html' not in rec.get('content_type', ''):
            continue
        soup = BeautifulSoup((BASE / 'CONTROLLED_SAMPLE' / rec['capture']).read_bytes().decode('cp1251'), 'html.parser')
        print('\nURL', rec['url'])
        for selector in ('.productGrid__name', '.productGrid__code', '.productGrid__brand',
                         '.productCost__price:not(.productCost__price_old)', '.productCost__price_old',
                         '.productCost__statusList', '.productModels', '.compareTable .thead',
                         '.productGrid__type', '[data-tab-content="description"]'):
            nodes = soup.select(selector)
            print(selector, len(nodes), [str(n)[:1400] for n in nodes[:2]])
        print('Tab attributes', [dict(n.attrs) for n in soup.select('[data-tab-content], [data-tab-body], [id="models"]')][:20])
        print('Tabs', [(n.name, dict(n.attrs), n.get_text(' ', strip=True)[:120]) for n in soup.select('.productTabs > *, .productTabs__content')][:20])
        print('Own table rows', [str(n)[:1600] for n in soup.select('.compareTable .tline')[:2]])
        print('Own preview', [str(n)[:1100] for n in soup.select('.productPreview a[href]')[:2]])
        cards = soup.select('.productCard a.productCard__name[href]')
        urls = ['https://www.stanki.ru' + a['href'] for a in cards]
        coverage.append({'url': rec['url'], 'listing_cards': len(urls), 'missing_in_sitemap': [u for u in urls if u not in sitemap]})
    print('listing sitemap comparison', json.dumps(coverage, ensure_ascii=False))
    (BASE / 'CONTROLLED_COVERAGE.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
