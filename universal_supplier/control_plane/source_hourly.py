"""Local SELECT/checkpoint reports for the existing LIVE11 queue; no collector or LLM."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import csv
import io
import json
import os
from .source_resume import digest


def atomic_text(path, text):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def bucket(status, auto):
    return 'AUTO' if auto else 'CONFLICT' if status == 'CONFLICT' else 'NEW' if status == 'NEW_CANDIDATE' else 'REVIEW'


def throughput(current, previous, seconds):
    rate = max(0, current - previous) * 3600 / seconds if seconds > 0 else None
    return rate


def checkpoint(path):
    if not path.exists(): return None
    value = json.loads(path.read_text(encoding='utf-8'))
    check = dict(value); checksum = check.pop('checksum')
    if digest(check) != checksum: raise ValueError('HOURLY_CHECKPOINT_CHECKSUM')
    return value


def collect(config, manifest, *, previous=None, worker_state='RUNNING', now=None):
    """Copy CP first, then open the SQL snapshot: never compare newer CP to older SQL."""
    import psycopg
    from .source_recovery import continuation_invariants, normal
    from .source_runtime import job_dir
    now = now or datetime.now(timezone.utc)
    with psycopg.connect(**config.kwargs(), connect_timeout=5, autocommit=True,
            options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
        from .source_canary import validate_config, validate_connection
        validate_config(config); validate_connection(c)
        jobs = c.execute('''SELECT DISTINCT ON (s.code,j.settings_snapshot->'source_batch'->>'phase')
            s.code,j.settings_snapshot->'source_batch'->>'phase',j.id,j.status,j.crawl_run_id,r.metadata,j.error_message,
            j.settings_snapshot->'crawl_settings'->>'resume_owner_job_id'
            FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
            WHERE j.settings_snapshot->'source_batch'->>'id'=%s
            ORDER BY s.code,j.settings_snapshot->'source_batch'->>'phase',j.id DESC''', (manifest['batch_id'],)).fetchall()
        copies = []
        for code, phase, jid, state, run, metadata, error, saved_owner in jobs:
            runtime = (metadata or {}).get('source_runtime', {})
            owner = runtime.get('owner_job_id') or (int(saved_owner) if saved_owner else jid)
            cp = checkpoint(job_dir(code, owner) / 'checkpoint.json')
            copies.append((code, phase, jid, state, runtime, error, cp))
        with c.transaction():
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with c.cursor() as q: continuation_invariants(q, manifest, config)
            durability = c.execute("SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit')").fetchone()
            if durability != ('on', 'on', 'on'): raise ValueError('HOURLY_POSTGRES_DURABILITY_REQUIRED')
            active = normal(c.execute("SELECT id,status FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id").fetchall())
            settings = normal(c.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code').fetchall())
            rows = []
            for parent in manifest['parents']:
                snap = parent['snapshot']; code = snap['supplier_code']
                phases = [x for x in copies if x[0] == code]
                receipts = {}; proven = set(); refresh = {}; quarantine = errors = cached = pending = inflight = 0
                for _, phase, jid, state, runtime, error, cp in phases:
                    if state in ('failed', 'stale_failed'): errors += 1
                    if not cp:
                        if phase == 'REFRESH': pending += len(snap['source_input']['entries'])
                        continue
                    inflight += len(cp['inflight']); pending += len(cp['frontier'])
                    if phase == 'REFRESH': refresh = cp['terminal']
                    for row in cp['terminal'].values():
                        kind = row['state']; receipt = row['receipt']
                        quarantine += kind == 'QUARANTINED'; errors += kind in ('FAILED', 'UNCERTAIN')
                        cached += kind == 'HTTP_CAPTURED_NOT_COMMITTED'
                        if kind != 'COMMITTED': continue
                        receipts[receipt['capture_fingerprint']] = receipt
                        if receipt.get('matching_receipt', {}).get('status') == 'succeeded': proven.add(receipt['external_id'])
                        if code == 'beka_mak' and jid in (28, 40) and state == 'succeeded' and runtime.get('identity_post_processing', {}).get('matching_reused'):
                            proven.add(receipt['external_id'])
                fps = list(receipts)
                saved = c.execute('''SELECT c.source_product_id,sp.external_id,c.capture_fingerprint FROM supplier_http_captures c
                    JOIN suppliers s ON s.id=c.supplier_id JOIN source_products sp ON sp.id=c.source_product_id
                    JOIN offer_commercial_observations o ON o.capture_id=c.id AND o.source_product_id=c.source_product_id
                    AND o.observed_at=c.observed_at WHERE s.code=%s AND c.capture_fingerprint=ANY(%s)''', (code, fps)).fetchall()
                if len(saved) != len(fps) or any(fp not in receipts or receipts[fp]['external_id'] != ext for _,ext,fp in saved):
                    raise ValueError('HOURLY_SQL_RECEIPT_MISMATCH')
                ids = sorted({r[0] for r in saved}); external = {r[1] for r in saved}
                matching = c.execute('SELECT source_product_id,status,auto_accepted FROM product_matches WHERE source_product_id=ANY(%s) AND is_current', (ids,)).fetchall()
                known_ids = {i for i, ext, fp in saved if ext in proven}
                buckets = Counter(bucket(status, auto) for i, status, auto in matching if i in known_ids)
                if len(known_ids) != sum(buckets.values()): raise ValueError('HOURLY_MATCHING_PROOF_MISMATCH')
                uncommitted = sum(refresh.get(e['url'], {}).get('state') != 'COMMITTED' for e in snap['source_input']['entries'])
                prior = next((r for r in (previous or {}).get('sources', []) if r['source'] == code), None)
                elapsed = (now - datetime.fromisoformat(previous['at'])).total_seconds() if previous else 0
                rate = throughput(len(fps), prior['committed'] if prior else len(fps), elapsed)
                remaining = pending + cached + inflight
                rows.append({'source': code, 'committed': len(fps), 'unique_products': len(ids),
                    **{key: buckets.get(key, 0) for key in ('AUTO', 'REVIEW', 'CONFLICT', 'NEW')},
                    'matching_pending': len(external - proven), 'quarantine': quarantine, 'errors': errors,
                    'cached_pending': cached, 'inflight': inflight, 'remaining_urls': remaining,
                    'uncommitted_refresh_urls': uncommitted, 'urls_per_hour': rate,
                    'eta_hours_estimate': remaining / rate if rate and rate > 0 else None,
                    'eta_basis': 'INFERENCE: known remaining queue only; unfinished discovery excluded',
                    'phases': [{'phase': p, 'job': j, 'state': s, 'reason': rt.get('termination_reason') or er, 'resume_available': rt.get('resume_available')} for _, p, j, s, rt, er, _ in phases]})
            return {'at': now.isoformat(), 'classification': 'LIVE-VERIFIED SELECT/checkpoint snapshot, no business writes',
                'batch_id': manifest['batch_id'], 'postgres': 'IDENTITY_VERIFIED', 'durability': dict(zip(('fsync','full_page_writes','synchronous_commit'),durability)), 'worker': worker_state,
                'original1282_exact': True, 'active_jobs': active, 'settings_restored_exact': settings == manifest['settings_before'],
                'sources': rows, 'committed': sum(r['committed'] for r in rows),
                'unique_products': sum(r['unique_products'] for r in rows), 'external_catalog_completeness': 'NOT_PROVEN'}


def save_report(folder, value, index):
    """Versioned report + atomic latest pointers. Never overwrite durable source CP."""
    folder = Path(folder); name = f'{index:03d}-{value["at"].replace(":", "").replace("+", "_")}'
    raw = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    atomic_text(folder / (name + '.json'), raw)
    atomic_text(folder / 'latest.json', raw)
    rows = value.get('sources') or [{'source':'__SESSION__','error':value.get('error','NO_SOURCE_REPORT')}]
    stream = io.StringIO(); fields = [k for k in rows[0] if k != 'phases']
    meta = {k:value.get(k,'UNVERIFIED') for k in ('at','postgres','worker','original1282_exact')}
    writer = csv.DictWriter(stream, fieldnames=[*meta, *fields]); writer.writeheader()
    for row in rows: writer.writerow({**meta, **{k: row[k] for k in fields}})
    atomic_text(folder / (name + '.csv'), stream.getvalue())
    atomic_text(folder / 'latest.csv', stream.getvalue())
