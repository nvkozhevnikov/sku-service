"""Resumable sequential product reconciliation. No SQL, scheduler or writes to Sterbrust.

Every successful response is saved before parsing. Reuse exact saved responses;
redirects never become duplicate source identities. A completed queue is not a
verified manifest if parse/identity failures remain.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup, UnicodeDammit
from universal_supplier.adapters.kami import KamiAdapter, canonical_source_url
from universal_supplier.adapters.kami import parse_listing
from universal_supplier.kami_robots import KamiRobots
from universal_supplier.models import FetchRecord
from scripts.discover_kami_catalog import traversal_url_is_safe
from universal_supplier.kami_checkpoint import write_checkpoint

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'
OUT = BASE / 'PRODUCT_RECONCILIATION'
AGENT = 'UniversalSupplier-KamiAudit/1.0'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-new-get', type=int, default=0)
    parser.add_argument('--coverage-resume', action='store_true',
                        help='Exactly one post-pass saved-category coverage replay')
    args = parser.parse_args()
    plan_path = BASE / 'PRODUCT_RECONCILIATION_PLAN.json'
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    if plan['status'] != 'PRODUCT_RECONCILIATION_REQUIRED_NOT_FULL_DISCOVERY_PASS':
        raise RuntimeError('Pinned complete listing traversal plan required')
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'cards').mkdir(exist_ok=True)
    checkpoint_path = OUT / 'CHECKPOINT.json'
    state = json.loads(checkpoint_path.read_bytes()) if checkpoint_path.exists() else {
        'plan_sha256': plan_sha, 'status': 'IN_PROGRESS', 'queue': plan['candidate_detail_urls'],
        'completed': {}, 'cards': {}, 'captures': [], 'identity_conflicts': [], 'parse_errors': [],
        'scope': plan['scope'], 'sql_writes': 0, 'full_RUN2': 'NOT_STARTED'}
    if state['plan_sha256'] != plan_sha:
        raise RuntimeError('Pinned reconciliation plan changed; explicit reconciliation required')
    if args.coverage_resume:
        if (state['status'] != 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED' or state['queue']
                or state.get('saved_category_resume_count', 0) != 0):
            raise RuntimeError('Coverage resume requires finished first pass and zero prior coverage resumes')
        state['saved_category_resume_count'] = 1
    elif state['status'] == 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED':
        raise RuntimeError('Use explicit --coverage-resume exactly once after first pass')
    state['status'], state['blocker'] = 'IN_PROGRESS', None
    session = requests.Session(); session.trust_env = False
    session.headers.update({'User-Agent': AGENT})
    last, pause, new_gets = 0.0, 1.0, 0
    saved = {}
    for folder in BASE.iterdir():
        if not folder.is_dir():
            continue
        audit = folder / 'HTTP_AUDIT.json'
        cp = folder / 'CHECKPOINT.json'
        records = json.loads(audit.read_bytes()) if audit.exists() else (
            json.loads(cp.read_bytes()).get('pages', []) if cp.exists() and folder != OUT else [])
        if folder == OUT:
            records = state['captures']
        for rec in records:
            if rec.get('status') == 200 and '/catalog/' in rec['url']:
                saved[rec['url']] = (folder, rec)

    def checkpoint():
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        state['summary'] = {'completed_urls': len(state['completed']), 'remaining_urls': len(state['queue']),
                            'source_identities': len(state['cards']), 'parse_errors': len(state['parse_errors']),
                            'identity_conflicts': len(state['identity_conflicts']), 'new_gets_this_process': new_gets}
        write_checkpoint(checkpoint_path, state)

    def get(url):
        nonlocal last, new_gets
        time.sleep(max(0.0, pause - (time.monotonic() - last)))
        response = session.get(url, allow_redirects=False, timeout=(15, 45))
        last = time.monotonic(); new_gets += 1
        raw = response.content; sha = hashlib.sha256(raw).hexdigest()
        (OUT / (sha + '.capture')).write_bytes(raw)
        rec = {'url': url, 'status': response.status_code, 'capture': sha + '.capture', 'sha256': sha,
               'content_type': response.headers.get('Content-Type', ''),
               'observed_at': datetime.now(timezone.utc).isoformat(),
               'elapsed_ms': round(response.elapsed.total_seconds() * 1000)}
        if response.is_redirect:
            rec['redirect_target'] = urljoin(url, response.headers.get('Location', ''))
        state['captures'].append(rec); checkpoint()
        return raw, rec

    try:
        raw, rec = get('https://www.stanki.ru/robots.txt')
        if rec['status'] != 200:
            raise RuntimeError('ROBOTS_HTTP_NOT_200')
        robot = KamiRobots(UnicodeDammit(raw).unicode_markup, AGENT.split('/')[0])
        pause = max(pause, robot.delay)
        # Replay category coverage from saved captures when collector code gains
        # additional category handling. No HTTP for already completed pages.
        for url, decision in list(state['completed'].items()):
            if not args.coverage_resume:
                break
            if decision['route'] != 'NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION':
                continue
            if url not in saved:
                raise RuntimeError('COMPLETED_CATEGORY_CAPTURE_MISSING')
            folder, rec = saved[url]
            raw_page = (folder / rec['capture']).read_bytes()
            if hashlib.sha256(raw_page).hexdigest() != rec['sha256']:
                raise RuntimeError('COMPLETED_CATEGORY_CAPTURE_SHA_MISMATCH')
            text = UnicodeDammit(raw_page, is_html=True).unicode_markup
            soup = BeautifulSoup(text, 'html.parser')
            targets = [c['url'] for c in parse_listing(text, url)]
            targets += [urljoin(url, a['href']).split('#', 1)[0]
                        for a in soup.select('a[data-pagination-button][href]')]
            for target in targets:
                if not traversal_url_is_safe(target) or not robot.can_fetch(target):
                    raise RuntimeError('REQUIRED_SAVED_CATEGORY_LINK_DENIED')
                if target not in state['completed'] and target not in state['queue']:
                    state['queue'].append(target)
        checkpoint()
        while state['queue']:
            url = state['queue'][0]
            if url in state['completed']:
                state['queue'].pop(0); continue
            shape_safe = traversal_url_is_safe(url) if '?' in url else canonical_source_url(url) == url
            if not shape_safe or not robot.can_fetch(url):
                raise RuntimeError('REQUIRED_PRODUCT_SCOPE_OR_ROBOTS_BLOCKER')
            if url in saved:
                folder, rec = saved[url]
                raw = (folder / rec['capture']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != rec['sha256']:
                    raise RuntimeError('SAVED_CAPTURE_SHA_MISMATCH')
            else:
                raw, rec = get(url)
            status = rec['status']
            if status in {403, 429}:
                raise RuntimeError(f'ACCESS_STOP_HTTP_{status}')
            if status in {301, 302, 303, 307, 308}:
                target = canonical_source_url(rec['redirect_target'])
                if not robot.can_fetch(target):
                    raise RuntimeError('REDIRECT_ROBOTS_DENIED')
                if target not in state['completed'] and target not in state['queue']:
                    state['queue'].append(target)
                state['completed'][url] = {'route': 'REDIRECT_ALIAS', 'target': target, 'capture': rec}
            elif status in {404, 410}:
                state['completed'][url] = {'route': 'SOURCE_STALE_URL', 'capture': rec}
            elif status != 200:
                raise RuntimeError(f'HTTP_{status}_COVERAGE_UNKNOWN')
            else:
                text = UnicodeDammit(raw, is_html=True).unicode_markup
                if not text or any(x in text.lower() for x in ('cf-chl-', 'verify you are human', 'access denied')):
                    raise RuntimeError('ANTI_BOT_OR_UNKNOWN_CONTENT_STOP')
                soup = BeautifulSoup(text, 'html.parser')
                if not soup.select_one('.productGrid[data-product-id]'):
                    # A sitemap location is not proof of a product. Retain actual
                    # category HTML for later coverage reconciliation, not a fake card.
                    state['completed'][url] = {'route': 'NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION', 'capture': rec}
                    # Deeper sitemap categories can expose additional products
                    # or ordinary pagination absent from the depth-two traversal.
                    targets = [c['url'] for c in parse_listing(text, url)]
                    targets += [urljoin(url, a['href']).split('#', 1)[0]
                                for a in soup.select('a[data-pagination-button][href]')]
                    for target in targets:
                        if not traversal_url_is_safe(target) or not robot.can_fetch(target):
                            raise RuntimeError('REQUIRED_DEEP_CATEGORY_LINK_DENIED')
                        if target not in state['completed'] and target not in state['queue']:
                            state['queue'].append(target)
                else:
                    try:
                        card = KamiAdapter().parse_product(FetchRecord(url, url, 200, (), 1, rec.get('elapsed_ms', 0), text))
                        payload = card.as_jsonable()
                        existing = state['cards'].get(card.external_id)
                        if existing and (existing['name'] != card.name or existing['full_model'] != card.raw_data['full_model']):
                            state['identity_conflicts'].append({'external_id': card.external_id, 'old_url': existing['canonical_url'], 'new_url': url})
                            route = 'SOURCE_IDENTITY_COLLISION_REVIEW'
                        else:
                            card_file = OUT / 'cards' / (hashlib.sha256(card.external_id.encode()).hexdigest() + '.json')
                            card_record = {'card': payload, 'capture': rec,
                                           'evidence_ref': str((folder if url in saved else OUT) / rec['capture'])}
                            if not existing:
                                card_file.write_text(json.dumps(card_record, ensure_ascii=False, indent=2), encoding='utf-8')
                            state['cards'].setdefault(card.external_id, {'name': card.name,
                                'full_model': card.raw_data['full_model'], 'canonical_url': card.canonical_url,
                                'payload_file': str(card_file.relative_to(OUT)),
                                'payload_sha256': hashlib.sha256(card_file.read_bytes()).hexdigest()})
                            route = 'PRODUCT_OBSERVED'
                        for variant in card.raw_data['variant_evidence']:
                            target = variant.get('url')
                            if target and target not in state['completed'] and target not in state['queue']:
                                state['queue'].append(target)
                        state['completed'][url] = {'route': route, 'external_id': card.external_id, 'capture': rec}
                    except ValueError as exc:
                        state['parse_errors'].append({'url': url, 'reason': str(exc), 'capture': rec})
                        state['completed'][url] = {'route': 'PARSER_REVIEW_REQUIRED', 'capture': rec}
            state['queue'].pop(0); checkpoint()
            if len(state['completed']) % 20 == 0:
                print(json.dumps(state['summary']), flush=True)
            if args.max_new_get and new_gets >= args.max_new_get:
                state['status'] = 'BOUNDED_CHECKPOINT_NOT_FULL_RUN1'; checkpoint(); return
        state['status'] = 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED'
        checkpoint()
    except Exception as exc:
        state['status'] = 'STOPPED_DISCOVERY_INCOMPLETE'
        state['blocker'] = {'type': type(exc).__name__, 'reason': str(exc), 'resume_from': state['queue'][0] if state['queue'] else None}
        checkpoint(); print(json.dumps(state['blocker']), flush=True); raise SystemExit(2)


if __name__ == '__main__':
    main()
