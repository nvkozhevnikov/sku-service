"""Live intervesp.ru sitemap discovery with a fully accounted URL ledger.

Only public GETs: robots.txt once, then the sitemap chain, 20s pauses, engine
validators. Mirrors the engine's intervesp rules: /catalog/ detail paths that
mention beka-mak, with the expected model taken from the URL slug. Every
discovered <loc> is accounted; UNRESOLVED must stay zero. No DB access.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.commercial_discovery import _MODEL_RE, normalise_model  # noqa: E402
from universal_supplier.full_supplier_collection import (  # noqa: E402
    PublicPacer, _candidate_sha, _locs, _safe_source_url, _sitemap_xml)
from universal_supplier.full_supplier_collection import FullCandidate  # noqa: E402
from universal_supplier.http_capture import UrllibPublicHttpClient  # noqa: E402

OUT = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08/INTERVESP_DISCOVERY'
SITE = 'intervesp'
ROOT_SITEMAP = 'https://intervesp.ru/sitemap.xml'
ROBOTS = 'https://intervesp.ru/robots.txt'
HOST = 'intervesp.ru'


def fetch_robots(client: UrllibPublicHttpClient, pacer: PublicPacer) -> dict:
    pacer.before_get()
    response = client.get(ROBOTS, timeout_seconds=30)
    if response.final_url != ROBOTS or urlparse(response.final_url).hostname != HOST:
        raise RuntimeError('robots.txt redirected away from the source host')
    if response.status_code != 200:
        raise RuntimeError(f'intervesp robots.txt returned HTTP {response.status_code}')
    body = response.body.decode('utf-8', 'replace')
    section, wildcard, sitemaps = [], False, []
    for raw in body.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'user-agent':
            wildcard = (value == '*')
            continue
        if key == 'sitemap':
            sitemaps.append(value)
        if wildcard and key in {'allow', 'disallow'} and value:
            section.append(f'{key}:{value}')
    blocking = [rule for rule in section if rule.startswith('disallow:') and rule != 'disallow:']
    return {'url': ROBOTS, 'http_status': response.status_code,
            'sha256': hashlib.sha256(body.encode()).hexdigest(),
            'sitemap_declarations': sitemaps, 'wildcard_rules': section,
            'blocking_rules': blocking, 'root_sitemap_declared': ROOT_SITEMAP in sitemaps}


def classify(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    try:
        _safe_source_url(SITE, url)
    except ValueError as error:
        return 'EXCLUDED_NON_PRODUCT', str(error)
    slug = parsed.path.rstrip('/').rsplit('/', 1)[-1].replace('_', '-')
    found = _MODEL_RE.search(slug)
    if not found:
        return 'EXCLUDED_NO_MODEL_IN_SLUG', 'no Bekamak model token in the URL slug'
    return 'PRODUCT_CANDIDATE', f'model {normalise_model(found.group(1))} from slug'


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = UrllibPublicHttpClient()
    pacer = PublicPacer()
    robots = fetch_robots(client, pacer)
    evidence: list[dict] = [robots]
    root = _sitemap_xml(SITE, ROOT_SITEMAP, client, pacer, evidence)
    root_kind = root.tag.rsplit('}', 1)[-1]
    if root_kind == 'sitemapindex':
        children = [url for url in _locs(root) if not urlparse(url).path.endswith('sitemap-files.xml')]
        if len(children) > 50:
            raise RuntimeError('intervesp sitemap index exceeds child limit')
    elif root_kind == 'urlset':
        children = [ROOT_SITEMAP]
    else:
        raise RuntimeError(f'intervesp sitemap has unexpected root {root_kind}')
    occurrences: Counter = Counter()
    first_seen: dict[str, str] = {}
    states: dict[str, str] = {}
    reasons: dict[str, str] = {}
    models: dict[str, str] = {}
    counts: dict[str, int] = {}
    for child_url in children:
        child = root if child_url == ROOT_SITEMAP else _sitemap_xml(SITE, child_url, client, pacer, evidence)
        if child.tag.rsplit('}', 1)[-1] != 'urlset':
            raise RuntimeError('intervesp nested sitemap is not a urlset')
        urls = _locs(child)
        counts[f'{SITE}:{urlparse(child_url).path}'] = len(urls)
        for url in urls:
            occurrences[url] += 1
            first_seen.setdefault(url, urlparse(child_url).path)
            if url in states:
                continue
            state, reason = classify(url)
            states[url] = state
            reasons[url] = reason
            if state == 'PRODUCT_CANDIDATE':
                models[url] = reason.split(' ')[1]
    blocked_by_robots = sorted(
        url for url in states
        if any(urlparse(url).path.startswith(rule.removeprefix('disallow:'))
               for rule in robots['blocking_rules']))
    if blocked_by_robots:
        raise RuntimeError(f'intervesp robots.txt Disallow covers product URLs: {blocked_by_robots[:5]}')
    duplicates = {url: n for url, n in occurrences.items() if n > 1}
    candidates = sorted(url for url, state in states.items() if state == 'PRODUCT_CANDIDATE')
    unresolved = sorted(url for url, state in states.items()
                        if state not in {'PRODUCT_CANDIDATE', 'EXCLUDED_NON_PRODUCT', 'EXCLUDED_NO_MODEL_IN_SLUG'})
    if unresolved:
        raise RuntimeError(f'unaccounted discovery URLs remain: {unresolved[:5]}')
    rows = [FullCandidate(SITE, url, models[url]) for url in candidates]
    manifest = {'format': 'supplier-full-manifest-v1', 'sha256': _candidate_sha(tuple(rows)),
                'candidates': [row.__dict__ for row in rows], 'sitemap_counts': counts, 'review_urls': []}
    (OUT / 'INTERVESP_DISCOVERY_MANIFEST.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'INTERVESP_DISCOVERY_LEDGER.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['url', 'first_sitemap', 'occurrences', 'state', 'reason'])
        for url in sorted(states):
            writer.writerow([url, first_seen[url], occurrences[url], states[url], reasons[url]])
    state_counts = Counter(states.values())
    excluded_reasons = Counter(reasons[url] for url in states if states[url] != 'PRODUCT_CANDIDATE')
    checkpoint = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'status': 'DISCOVERY_VERIFIED' if not unresolved else 'DISCOVERY_UNRESOLVED',
        'site': SITE, 'http_gets': 2 + (0 if root_kind == 'urlset' else len(children)),
        'robots': robots, 'sitemap_evidence': evidence[1:],
        'sitemap_counts': counts, 'total_distinct_urls': len(states),
        'total_occurrences': sum(occurrences.values()), 'duplicate_urls': len(duplicates),
        'state_counts': dict(state_counts), 'excluded_reasons': dict(excluded_reasons),
        'product_candidates': len(candidates), 'unresolved': len(unresolved),
        'robots_blocked_product_urls': len(blocked_by_robots),
        'manifest_sha256': manifest['sha256'],
    }
    (OUT / 'INTERVESP_DISCOVERY_CHECKPOINT.json').write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: checkpoint[k] for k in
                      ('status', 'product_candidates', 'total_distinct_urls', 'state_counts',
                       'duplicate_urls', 'robots_blocked_product_urls', 'manifest_sha256')},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
