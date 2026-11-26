"""Authorized read-only source audit for the LTT/USS ingestion stage.

Public GETs only: robots.txt, sitemap chain and a small deterministic sample
of pages per source (>= 20 s pause between requests, one attempt, bounded
redirects, no cookies/proxies).  Raw bytes are stored under reports/ as frozen
evidence for adapter development; the JSON ledger records the actual HTTP
outcome for every request.  No database access, no parsing side effects.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.full_supplier_collection import PublicPacer  # noqa: E402
from universal_supplier.http_capture import UrllibPublicHttpClient  # noqa: E402

OUT = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13/AUDIT'
RAW = OUT / 'RAW'

SOURCES = {
    'ltt_msk': {
        'base': 'https://ltt-msk.ru/',
        'robots': 'https://ltt-msk.ru/robots.txt',
        'sitemaps': ['https://ltt-msk.ru/sitemap.xml'],
        'hosts': {'ltt-msk.ru'},
    },
    'ltt_sib': {
        'base': 'https://lttsib.ru/',
        'robots': 'https://lttsib.ru/robots.txt',
        'sitemaps': ['https://lttsib.ru/sitemap.xml'],
        'hosts': {'lttsib.ru'},
    },
    'ltt_yug': {
        'base': 'https://ltt-yug.ru/',
        'robots': 'https://www.ltt-yug.ru/robots.txt',
        'sitemaps': ['https://ltt-yug.ru/sitemap.xml'],
        'hosts': {'ltt-yug.ru', 'www.ltt-yug.ru'},
    },
    'uss_stanko': {
        'base': 'https://uss-stanko.com/',
        'robots': 'https://uss-stanko.com/robots.txt',
        'sitemaps': ['https://uss-stanko.com/sitemap.xml'],
        'hosts': {'uss-stanko.com', 'www.uss-stanko.com'},
    },
}

# Deterministic audit samples: (label, exact URL) pairs.  Kept explicit so the
# audit is reproducible; each source also gets sitemap-derived picks below.
FIXED_SAMPLES = {
    'ltt_msk': [
        ('home', 'https://ltt-msk.ru/'),
        ('machine_numeric_price',
         'https://ltt-msk.ru/katalog/stolyarnoe-oborudovanie/rejsmusovyie-stanki/'
         'rejsmusovyij-stanok-mv1013a,-1300-mm/ml9326cm'),
    ],
    'ltt_sib': [
        ('home', 'https://lttsib.ru/'),
        ('machine_por_outofstock',
         'https://lttsib.ru/katalog/stolyarnoe-oborudovanie/rejsmusovyie-stanki/rejsmusovyij-stanok-mv104n'),
    ],
    'ltt_yug': [
        ('home', 'https://ltt-yug.ru/'),
        ('machine_used_qty1',
         'https://ltt-yug.ru/catalog/mebelnoe-i-stolyarnoe-oborudovanie/kromkooblitsovochnye/'
         'avtomaticheskiy-kromkooblitsovochnyy-stanok-ltt-230-rub.html'),
    ],
    'uss_stanko': [
        ('home', 'https://uss-stanko.com/'),
        ('category_radial_drilling_operator_example',
         'https://uss-stanko.com/mach/sver/radialno_sverlilnie_stanki.htm'),
    ],
}

# Sitemap-derived picks: (label, predicate on lowercase url).
PATTERN_SAMPLES = {
    'ltt_msk': [
        ('spare_accessory', lambda u: '/zapasnyie-chasti/' in u and 'rolik' in u),
        ('tool_blade', lambda u: '/instrument/' in u and 'freza-radiusnaya' in u),
        ('compressor', lambda u: 'kompressor-vintovoj' in u),
    ],
    'ltt_sib': [
        ('machine_numeric_mb203c', lambda u: 'mb203c' in u and 'rejsmusovyie' in u),
        ('consumable_drill', lambda u: 'gluxoe-sverlo' in u),
        ('metal_bandsaw', lambda u: 'lentochnopilnyij-stanok-po-metallu' in u),
    ],
    'ltt_yug': [
        ('machine_fugoval_jp801', lambda u: 'jp801' in u),
        ('machine_numeric_mf362', lambda u: 'mf362' in u),
        ('machine_nonrub_slug_variant', lambda u: not u.endswith('-rub.html') and '/kromkooblitsovochnye/' in u),
    ],
    'uss_stanko': [
        ('dmtg_detail', lambda u: '/dmtg/' in u and not u.endswith('index.htm')),
        ('mrcm_detail', lambda u: '/mrcm/' in u and not u.endswith('index.htm')),
        ('unikraft_accessory', lambda u: '/unikraft/' in u and not u.endswith('index.htm')),
    ],
}


def slug_for(url: str) -> str:
    parsed = urlparse(url)
    tail = parsed.path.rstrip('/').rsplit('/', 1)[-1] or 'root'
    tail = re.sub(r'[^0-9A-Za-z_.-]+', '_', tail)[:80]
    return f'{parsed.hostname}_{tail}'


def get_once(client: UrllibPublicHttpClient, pacer: PublicPacer, url: str, ledger: list,
             source: str, label: str, *, timeout: float = 30.0) -> bytes | None:
    started = time.monotonic()
    try:
        response = client.get(url, timeout_seconds=timeout)
        elapsed = round((time.monotonic() - started) * 1000)
        body = response.body
        entry = {
            'source': source, 'label': label, 'url': url, 'final_url': response.final_url,
            'http_status': response.status_code, 'content_type': response.content_type,
            'redirects': list(response.redirects), 'bytes': len(body), 'elapsed_ms': elapsed,
            'sha256': hashlib.sha256(body).hexdigest(),
        }
    except Exception as error:  # network level; record and continue
        elapsed = round((time.monotonic() - started) * 1000)
        entry = {'source': source, 'label': label, 'url': url, 'final_url': None,
                 'http_status': None, 'content_type': None, 'redirects': [],
                 'bytes': 0, 'elapsed_ms': elapsed,
                 'error': f'{type(error).__name__}: {error}'}
        body = None
    ledger.append(entry)
    print(json.dumps({k: entry[k] for k in ('source', 'label', 'http_status', 'bytes', 'elapsed_ms')},
                     ensure_ascii=False), flush=True)
    if entry['http_status'] in (403, 429):
        raise RuntimeError(f'{source} returned HTTP {entry["http_status"]} for {url}; stopping this source')
    return body



def audit_source(source: str, config: dict, client: UrllibPublicHttpClient,
                 pacer: PublicPacer, ledger: list) -> dict:
    result: dict = {'source': source, 'base_url': config['base']}
    # 1. robots.txt
    robots_body = get_once(client, pacer, config['robots'], ledger, source, 'robots_txt')
    if robots_body is not None:
        (RAW / f'{source}_robots.txt').write_bytes(robots_body)
        text = robots_body.decode('utf-8', 'replace')
        sitemaps = [line.split(':', 1)[1].strip() for line in text.splitlines()
                    if line.lower().startswith('sitemap:')]
        disallows = [line for line in text.splitlines() if line.lower().startswith('disallow')]
        result['robots'] = {'http_ok': True, 'declared_sitemaps': sitemaps, 'disallow_lines': disallows}
    else:
        result['robots'] = {'http_ok': False}
        return result

    # 2. sitemap chain (follow one index level, same-host children only)
    locs: list[str] = []
    sitemap_entries = []
    queue = list(config['sitemaps'])
    seen_sitemaps = set()
    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        if urlparse(sitemap_url).hostname not in config['hosts']:
            continue
        body = get_once(client, pacer, sitemap_url, ledger, source, f'sitemap:{sitemap_url}')
        if body is None:
            continue
        (RAW / f'{slug_for(sitemap_url)}.xml').write_bytes(body)
        root = ET.fromstring(body)
        kind = root.tag.rsplit('}', 1)[-1]
        child_locs = [item.text.strip() for item in root.iter()
                      if item.tag.rsplit('}', 1)[-1] == 'loc' and item.text and item.text.strip()]
        sitemap_entries.append({'url': sitemap_url, 'kind': kind, 'locs': len(child_locs)})
        if kind == 'sitemapindex':
            queue.extend(child_locs)
        else:
            locs.extend(child_locs)
    result['sitemaps'] = sitemap_entries
    unique_locs = sorted(set(locs))
    result['total_locs'] = len(locs)
    result['distinct_locs'] = len(unique_locs)
    result['duplicate_locs'] = len(locs) - len(unique_locs)

    # 3. path shape analysis
    segment_counts: dict[str, int] = {}
    for url in unique_locs:
        path = urlparse(url).path
        parts = path.strip('/').split('/')
        first = parts[0] if parts and parts[0] else '(root)'
        segment_counts[first] = segment_counts.get(first, 0) + 1
    result['path_roots'] = dict(sorted(segment_counts.items(), key=lambda kv: -kv[1]))
    result['query_strings'] = sum(1 for u in unique_locs if urlparse(u).query)
    result['non_https'] = sum(1 for u in unique_locs if urlparse(u).scheme != 'https')

    # 4. deterministic sample pages
    samples: list[tuple[str, str]] = list(FIXED_SAMPLES[source])
    for label, predicate in PATTERN_SAMPLES[source]:
        match = next((u for u in unique_locs if predicate(u)), None)
        if match is None:
            result.setdefault('sample_misses', []).append(label)
        else:
            samples.append((label, match))
    for label, url in samples:
        if urlparse(url).hostname not in config['hosts']:
            result.setdefault('sample_rejected', []).append({'label': label, 'url': url})
            continue
        body = get_once(client, pacer, url, ledger, source, f'sample:{label}')
        if body is not None:
            (RAW / f'{source}__{label}.html').write_bytes(body)
            head = body[:4096].decode('ascii', 'replace')
            charset = re.search(r'charset=["\']?([\w-]+)', head, re.I)
            result.setdefault('sample_charsets', {})[label] = (charset.group(1).lower()
                                                               if charset else 'not-declared')
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    client = UrllibPublicHttpClient()
    pacer = PublicPacer()  # fixed >= 20 s pause, audit never speeds up
    ledger: list[dict] = []
    summaries = []
    blocked = []
    for source, config in SOURCES.items():
        try:
            summaries.append(audit_source(source, config, client, pacer, ledger))
        except RuntimeError as error:
            blocked.append(str(error))
            summaries.append({'source': source, 'status': 'STOPPED', 'error': str(error)})
    summary = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'http_gets': len(ledger),
        'sources': summaries,
        'blocked': blocked,
        'ledger': ledger,
    }
    (OUT / 'SOURCE_AUDIT_SUMMARY.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'AUDIT_DONE', 'http_gets': len(ledger), 'blocked': blocked},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
