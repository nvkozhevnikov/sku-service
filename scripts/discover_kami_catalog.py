"""Resumable source-wide category/listing discovery. GET only, sequential.

Sitemap seeds are candidates, never automatically exact product identities.
Every checkpoint states whether traversal actually completed. No DB writes.
"""
from __future__ import annotations
import hashlib
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl
import requests
from bs4 import BeautifulSoup, UnicodeDammit
from universal_supplier.adapters.kami import parse_listing
from universal_supplier.kami_robots import KamiRobots

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'
OUT = BASE / 'CATALOG_DISCOVERY'
AGENT = 'UniversalSupplier-KamiAudit/1.0'


def traversal_url_is_safe(url):
    """Only ordinary main-host catalog/used listings and plain pagination."""
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError:
        return False
    query = parse_qsl(p.query, keep_blank_values=True)
    return (p.scheme == 'https' and p.hostname == 'www.stanki.ru'
            and port is None and not p.username and not p.password and not p.fragment
            and p.path.startswith(('/catalog/', '/bu/'))
            and all(k.startswith('PAGEN_') and v.isdigit() for k, v in query))


def append_listing_evidence(entry, evidence):
    # A crash after parsing but before page completion must not duplicate evidence.
    if evidence not in entry['listing_records']:
        entry['listing_records'].append(evidence)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interval', type=float, default=5.0)
    parser.add_argument('--rate-probe-pages', type=int, default=0)
    args = parser.parse_args()
    if args.interval < 1.0 or args.rate_probe_pages < 0:
        raise ValueError('Sequential minimum 1 second; no parallel/zero-delay mode')
    OUT.mkdir(parents=True, exist_ok=True)
    state_path = OUT / 'CHECKPOINT.json'
    inventory = json.loads((BASE / 'SITEMAP_SCOPE_INVENTORY.json').read_text(encoding='utf-8'))
    seeds = sorted(u for u in inventory['candidate_catalog_urls'] if len(urlsplit(u).path.strip('/').split('/')) == 2)
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'status': 'DISCOVERY_INCOMPLETE', 'queue': ['https://www.stanki.ru/catalog/', *seeds],
        'completed': [], 'pages': [], 'products': {}, 'excluded': [], 'blocker': None}
    # Used equipment is on the same host; product links still use /catalog/.
    # Reuse existing root captures and never expand to zip.stanki.ru.
    for url in ['https://www.stanki.ru/bu/']:
        if url not in state['completed'] and url not in state['queue']:
            state['queue'].append(url)
    state['status'] = 'DISCOVERY_IN_PROGRESS'
    state['blocker'] = None
    session = requests.Session(); session.trust_env = False
    session.headers.update({'User-Agent': AGENT})
    pause, last = args.interval, 0.0
    initial_completed = len(state['completed'])

    def checkpoint():
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        state['summary'] = {'queued_pages': len(state['queue']), 'completed_pages': len(state['completed']),
                            'listing_product_urls': len(state['products']), 'excluded': len(state['excluded']),
                            'http_statuses': {str(s): sum(p.get('status') == s for p in state['pages'])
                                              for s in set(p.get('status') for p in state['pages'])}}
        temp = state_path.with_suffix('.partial')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(state_path)

    def request(url):
        nonlocal last
        time.sleep(max(0, pause - (time.monotonic() - last)))
        response = session.get(url, allow_redirects=False, timeout=(15, 45)); last = time.monotonic()
        raw = response.content; digest = hashlib.sha256(raw).hexdigest()
        (OUT / (digest + '.capture')).write_bytes(raw)
        rec = {'url': url, 'status': response.status_code, 'capture': digest + '.capture',
               'content_type': response.headers.get('Content-Type', ''),
               'elapsed_ms': round(response.elapsed.total_seconds() * 1000),
               'sha256': digest, 'observed_at': datetime.now(timezone.utc).isoformat()}
        if response.is_redirect:
            rec['redirect_target'] = urljoin(url, response.headers.get('Location', ''))
        state['pages'].append(rec); checkpoint()
        return response, rec

    try:
        response, record = request('https://www.stanki.ru/robots.txt')
        if response.status_code != 200:
            raise RuntimeError('ROBOTS_HTTP_NOT_200')
        robot = KamiRobots(response.text, AGENT.split('/')[0]); pause = max(pause, robot.delay)

        def approved(url):
            if not traversal_url_is_safe(url):
                return False
            if not robot.can_fetch(url):
                return False
            return True

        # Reuse pinned current audit pages; SHA is checked before trusting HTML.
        saved = {}
        for folder in (BASE / 'FULL_SITEMAPS', BASE / 'CONTROLLED_SAMPLE', BASE / 'SPECTR_VARIANT_SAMPLE'):
            if not (folder / 'HTTP_AUDIT.json').exists():
                continue
            for rec in json.loads((folder / 'HTTP_AUDIT.json').read_text(encoding='utf-8')):
                if rec.get('status') == 200 and 'html' in rec.get('content_type', ''):
                    saved[rec['url']] = (folder, rec)
        for rec in state['pages']:
            if rec.get('status') == 200 and urlsplit(rec['url']).path.startswith(('/catalog/', '/bu/')):
                saved[rec['url']] = (OUT, rec)
        while state['queue']:
            url = state['queue'][0]
            if url in state['completed']:
                state['queue'].pop(0); continue
            if not approved(url):
                state['excluded'].append({'url': url, 'reason': 'ROBOTS_DENIED_UNRESOLVED_COVERAGE'})
                raise RuntimeError('ROBOTS_DENIED_REQUIRED_CATEGORY_OR_PAGE')
            if url in saved:
                folder, record = saved[url]; raw = (folder / record['capture']).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == record['sha256']
                html = UnicodeDammit(raw, is_html=True).unicode_markup
                if html is None:
                    raise RuntimeError('SAVED_HTML_ENCODING_UNKNOWN')
                status, target = 200, None
            else:
                response, record = request(url); html = response.text; status = response.status_code
                target = record.get('redirect_target')
            if status in {403, 429}:
                raise RuntimeError(f'SOURCE_ACCESS_STOP_HTTP_{status}')
            if status in {301, 302, 303, 307, 308}:
                if not target or not approved(target):
                    raise RuntimeError('REDIRECT_TARGET_REQUIRES_SCOPE_OR_ROBOTS_ASSESSMENT')
                if target not in state['queue'] and target not in state['completed']:
                    state['queue'].append(target)
            elif status in {404, 410}:
                state['excluded'].append({'url': url, 'reason': f'STALE_SITEMAP_HTTP_{status}'})
            elif status != 200:
                raise RuntimeError(f'SOURCE_HTTP_{status}_COVERAGE_UNKNOWN')
            else:
                soup = BeautifulSoup(html, 'html.parser')
                if any(x in html.lower() for x in ('cf-chl-', 'verify you are human', 'access denied')):
                    raise RuntimeError('SOURCE_ANTI_BOT_STOP')
                cards = parse_listing(html, url)
                for card in cards:
                    entry = state['products'].setdefault(card['url'], {'listing_records': []})
                    evidence = {**card, 'category_page': url, 'capture_sha256': record['sha256']}
                    if urlsplit(url).path.startswith('/bu/'):
                        evidence['used_listing_context'] = True
                    append_listing_evidence(entry, evidence)
                # Ordinary links only. No prohibited AJAX/filter/sort endpoint.
                for a in soup.select('a[href]'):
                    next_url = urljoin(url, a['href']).split('#', 1)[0]
                    p = urlsplit(next_url)
                    is_category = p.path.startswith(('/catalog/', '/bu/')) and len(p.path.strip('/').split('/')) <= 2
                    is_page = a.has_attr('data-pagination-button') and bool(p.query)
                    if not (is_category or is_page) or p.hostname != 'www.stanki.ru':
                        continue
                    if next_url not in state['completed'] and next_url not in state['queue']:
                        # Filter/sort are intentionally excluded, not rewritten into
                        # another request that might change the catalog semantics.
                        if approved(next_url):
                            state['queue'].append(next_url)
                        elif is_page:
                            raise RuntimeError('ROBOTS_DENIED_REQUIRED_PAGINATION')
            state['completed'].append(url); state['queue'].pop(0); checkpoint()
            if args.rate_probe_pages and len(state['completed']) - initial_completed >= args.rate_probe_pages:
                state['status'] = 'DISCOVERY_INCOMPLETE_BOUNDED_RATE_PROBE_COMPLETE'
                state['rate_probe'] = {'interval': pause, 'completed_pages': args.rate_probe_pages,
                                       'no_parallel_requests': True, 'throttling_observed': False}
                state['blocker'] = None; checkpoint()
                print(json.dumps(state['rate_probe']), flush=True)
                return
            if len(state['completed']) % 10 == 0:
                print(json.dumps(state['summary']), flush=True)
        state['status'] = 'LISTING_TRAVERSAL_COMPLETE_PRODUCT_RECONCILIATION_PENDING'
        state['blocker'] = None; checkpoint()
    except Exception as exc:
        state['status'] = 'DISCOVERY_INCOMPLETE'
        state['blocker'] = {'type': type(exc).__name__, 'reason': str(exc), 'resume_from': state['queue'][0] if state['queue'] else None}
        checkpoint()
        print(json.dumps({'status': state['status'], 'blocker': state['blocker'], 'summary': state['summary']}, ensure_ascii=False), flush=True)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
