"""Exactly one sequential live RUN2 over the verified KAMI identity set.

No discovery/category GET; captured responses are reused on interrupted resume.
No source identity changes, missing deactivation or canonical assignment.
"""
import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests
from bs4 import UnicodeDammit
from scripts.persist_kami_full_run1 import ROOT, OUT, INPUT, PRIVATE, preflight, runtime_preflight, complete_fingerprint
from scripts.verify_kami_canary import fingerprint
from universal_supplier.adapters.kami import KamiAdapter, canonical_source_url
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.kami_robots import KamiRobots
from universal_supplier.models import FetchRecord
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.kami_checkpoint import write_checkpoint


def main():
    run1 = json.loads((OUT / 'FULL_RUN1_CHECKPOINT.json').read_bytes())
    proof = json.loads((INPUT / 'FINAL_DISCOVERY_VERIFIED.json').read_bytes())
    if (run1['status'] != 'LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY'
            or run1['cards_sha256'] != proof['cards_sha256']):
        raise RuntimeError('Verified full isolated RUN1 required before live RUN2')
    data = (INPUT / 'CARDS.jsonl').read_bytes()
    if hashlib.sha256(data).hexdigest() != proof['cards_sha256']:
        raise RuntimeError('Pinned RUN1 cards changed')
    cards = [json.loads(line) for line in data.decode('utf-8').splitlines()]
    folder = OUT / 'LIVE_RUN2'
    folder.mkdir(exist_ok=True)
    (folder / 'cards').mkdir(exist_ok=True)
    path = folder / 'CHECKPOINT.json'
    state = json.loads(path.read_bytes()) if path.exists() else {
        'cards_sha256': proof['cards_sha256'], 'status': 'IN_PROGRESS',
        'completed': {}, 'captured': {}, 'http_methods': {'GET': 0},
        'discovery_HTTP': 0, 'category_HTTP': 0, 'canonical_writes': 0}
    if state['cards_sha256'] != proof['cards_sha256'] or state['status'] == 'LIVE_RUN2_COMPLETE':
        raise RuntimeError('Cannot start another RUN2 or change pinned identities')

    def checkpoint():
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        write_checkpoint(path, state)

    config = json.loads((PRIVATE / 'runtime.json').read_bytes())
    if (config['DB_NAME'] != 'universal_supplier_kami_rc' or str(config['DB_PORT']) != '55451'
            or config.get('DB_HOST') != '127.0.0.1' or config.get('DB_USER') != 'kami_app'):
        raise RuntimeError('Exact isolated target required')
    marker = json.loads((OUT / 'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    admin = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
    expected = json.loads((OUT / 'VARIANT_CANARY_VERIFIED.json').read_bytes())['baseline_hashes']
    os.environ.update(config)
    session = requests.Session(); session.trust_env = False
    session.headers['User-Agent'] = 'UniversalSupplier-KamiAudit/1.0'
    last = 0.0
    delay = 1.0

    def get(url):
        nonlocal last
        time.sleep(max(0, delay - (time.monotonic() - last)))
        response = session.get(url, allow_redirects=False, timeout=(15, 45))
        last = time.monotonic()
        state['http_methods']['GET'] += 1
        return response

    repo = None
    try:
        with psycopg.connect(host='127.0.0.1', port=55451, dbname=config['DB_NAME'], user='kami_admin',
                password=admin, connect_timeout=5, autocommit=True) as conn:
            preflight(conn, marker)
            if fingerprint(conn, True) != expected:
                raise RuntimeError('Old namespace drift before live RUN2')
            response = get('https://www.stanki.ru/robots.txt')
            if response.status_code != 200:
                raise RuntimeError('Fresh robots required')
            robot_sha = hashlib.sha256(response.content).hexdigest()
            (folder / (robot_sha + '.robots')).write_bytes(response.content)
            state['robots'] = {'sha256': robot_sha, 'status': 200,
                               'evidence_ref': str((folder / (robot_sha + '.robots')).relative_to(ROOT))}
            robot = KamiRobots(UnicodeDammit(response.content).unicode_markup, 'UniversalSupplier-KamiAudit')
            delay = max(1.0, robot.delay)
            repo = PostgresRepository(PostgresConfig.from_env()); repo.gate()
            runtime_preflight(repo)
            for item in cards:
                old = item['card']; key = old['external_id']; url = old['canonical_url']
                if key in state['completed']:
                    continue
                preflight(conn, marker)
                if canonical_source_url(url) != url or not robot.can_fetch(url):
                    raise RuntimeError('RUN2 URL scope/robots STOP')
                rec = state['captured'].get(key)
                if rec is None:
                    response = get(url)
                    raw = response.content
                    sha = hashlib.sha256(raw).hexdigest()
                    (folder / (sha + '.capture')).write_bytes(raw)
                    rec = {'url': url, 'status': response.status_code, 'sha256': sha,
                           'capture': sha + '.capture', 'content_type': response.headers.get('Content-Type', ''),
                           'observed_at': datetime.now(timezone.utc).isoformat()}
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        rec['redirect_target'] = urljoin(url, response.headers.get('Location', ''))
                    state['captured'][key] = rec; checkpoint()
                raw = (folder / rec['capture']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != rec['sha256']:
                    raise RuntimeError('RUN2 saved capture SHA mismatch')
                if rec['status'] in {403, 429}:
                    raise RuntimeError(f'ACCESS_STOP_HTTP_{rec["status"]}')
                if rec['status'] >= 500:
                    raise RuntimeError(f'HTTP_{rec["status"]}_COVERAGE_UNKNOWN')
                if rec['status'] != 200:
                    state['completed'][key] = {'route': 'SOURCE_STALE_URL' if rec['status'] in {404,410}
                                               else 'HTTP_REVIEW', 'capture': rec}
                    checkpoint(); continue
                text = UnicodeDammit(raw, is_html=True).unicode_markup
                if not text or any(x in text.lower() for x in ('cf-chl-', 'verify you are human', 'access denied')):
                    raise RuntimeError('Anti-bot/unknown content STOP')
                try:
                    card = KamiAdapter().parse_product(FetchRecord(url, url, 200, (), 1, 0, text))
                except ValueError as exc:
                    state['completed'][key] = {'route': 'SOURCE_IDENTITY_REVIEW', 'reason': str(exc), 'capture': rec}
                    checkpoint(); continue
                if card.external_id != key or card.raw_data['full_model'] != old['raw_data']['full_model']:
                    state['completed'][key] = {'route': 'SOURCE_IDENTITY_REVIEW',
                        'reason': 'RUN2_VISIBLE_IDENTITY_CHANGED', 'capture': rec, 'observed_card': card.as_jsonable()}
                    checkpoint(); continue
                evidence_ref = str((folder / rec['capture']).relative_to(ROOT))
                capture = HttpCapture(url, url, 200, rec['content_type'], datetime.fromisoformat(rec['observed_at']),
                    rec['sha256'], rec['sha256'], evidence_ref)
                preflight(conn, marker)
                runtime_preflight(repo)
                result = repo.persist_kami_observation(card, capture)
                replay = repo.persist_kami_observation(card, capture)
                if not replay['exact_noop']:
                    raise RuntimeError('RUN2 exact replay not a no-op')
                card_path = folder / 'cards' / (hashlib.sha256(key.encode()).hexdigest() + '.json')
                card_path.write_text(json.dumps(card.as_jsonable(), ensure_ascii=False, indent=2), encoding='utf-8')
                state['completed'][key] = {'route': 'OBSERVED_PERSISTED', 'capture': rec,
                    'counts': asdict(result['ingest_counts']), 'replay_exact_noop': True,
                    'card_file': str(card_path.relative_to(folder)),
                    'card_sha256': hashlib.sha256(card_path.read_bytes()).hexdigest(),
                    'evidence_ref': evidence_ref}
                checkpoint()
                if len(state['completed']) % 20 == 0:
                    print(json.dumps({'RUN2_completed': len(state['completed']), 'total': len(cards)}), flush=True)
            preflight(conn, marker)
            if fingerprint(conn, True) != expected:
                raise RuntimeError('Old five namespaces changed')
            actual = conn.execute("SELECT count(*) FROM source_products WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')").fetchone()[0]
            if actual != len(cards):
                raise RuntimeError('Source cardinality changed during RUN2')
            # Replay only the successfully persisted RUN2 captures; no new GET.
            replay_before = complete_fingerprint(conn)
            replay_count = 0
            from universal_supplier.models import ProductCard
            for result in state['completed'].values():
                if result['route'] != 'OBSERVED_PERSISTED':
                    continue
                card_path = folder / result['card_file']
                if hashlib.sha256(card_path.read_bytes()).hexdigest() != result['card_sha256']:
                    raise RuntimeError('Pinned RUN2 card SHA mismatch')
                card = ProductCard.from_jsonable(json.loads(card_path.read_bytes()))
                rec = result['capture']
                capture = HttpCapture(rec['url'], rec['url'], 200, rec['content_type'],
                    datetime.fromisoformat(rec['observed_at']), rec['sha256'], rec['sha256'], result['evidence_ref'])
                preflight(conn, marker)
                runtime_preflight(repo)
                if not repo.persist_kami_observation(card, capture)['exact_noop']:
                    raise RuntimeError('Whole RUN2 saved replay changed state')
                replay_count += 1
            if complete_fingerprint(conn) != replay_before:
                raise RuntimeError('Whole RUN2 replay table/sequence hashes changed')
            aggregate = Counter()
            for result in state['completed'].values():
                aggregate.update(result.get('counts', {}))
            state.update(status='LIVE_RUN2_COMPLETE', source_rows_before=len(cards), source_rows_after=actual,
                         new_source_identity_delta=0, baseline_preserved=True,
                         ingest_delta_counts=dict(aggregate), full_saved_replay_rows=replay_count,
                         full_saved_replay_exact_noop=True, full_saved_replay_hashes=replay_before,
                         HTTP_statuses=dict(Counter(r['status'] for r in state['captured'].values())),
                         routes=dict(Counter(v['route'] for v in state['completed'].values())))
            checkpoint()
    except Exception as exc:
        state.update(status='STOPPED_RUN2_INCOMPLETE', blocker={'type': type(exc).__name__,
                    'reason': str(exc) if not isinstance(exc, requests.RequestException) else 'HTTP transport failed; URL/headers hidden'})
        checkpoint(); print(json.dumps(state['blocker']), flush=True); raise SystemExit(2)
    finally:
        if repo is not None:
            repo.close()


if __name__ == '__main__':
    main()
