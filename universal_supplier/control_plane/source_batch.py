"""LIVE11 envelopes over crawl_jobs. No second scheduler, tables or activation.

All mutations are explicit queue operations. Preparation uses existing read-only
preview/target guards; stored phase snapshots, not current defaults, feed workers.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import hashlib
import json
import subprocess
from uuid import UUID

from .source_resume import digest
from .source_canary import build_preview, validate_config, validate_connection, TARGET
from .source_jobs import RUNTIME_SOURCES
from .queue import normalized_domain

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / 'config/live11_approval_matrix.csv'
MATRIX_SHA = '4313bce92e0d448066d5b3b12277aa37f5cded1016503e53cd41b4f937051195'
SOURCE_ORDER = ('beka_mak','beka_mak_tr','vekprom','kami','intervesp','ltt_sib',
                'partner_st','optimum','ltt_yug','ltt_msk','uss_stanko')
BEKA_GATE_ACTION = 'SOURCE_BATCH_BEKA_ISOLATION_CONFIRMED'
STATES_RU = {'queued': 'ожидает', 'running': 'выполняется',
             'cancelled': 'остановлено', 'succeeded': 'завершено',
             'failed': 'ошибка', 'stale_failed': 'требуется решение оператора',
             'partial': 'требуется решение оператора',
             'skipped_locked': 'требуется решение оператора'}


def commercial_change_counts(history):
    from universal_supplier.change_detection import commercial_history_value
    price = stock = 0
    for old, new in history:
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        old, new = commercial_history_value(old), commercial_history_value(new)
        price += any(old.get(k) != new.get(k) for k in ('price','old_price','currency'))
        stock += any(old.get(k) != new.get(k) for k in ('availability_normalized','quantity'))
    return {'batch_price_changes': price, 'batch_stock_changes': stock}


def matrix_rows():
    raw = MATRIX.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MATRIX_SHA:
        raise ValueError('BATCH_MATRIX_CHANGED_REQUIRES_APPROVAL')
    rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    if len(rows) != 11 or {r['SOURCE'] for r in rows} != set(RUNTIME_SOURCES):
        raise ValueError('BATCH_EXPECTS_EXACT_ELEVEN')
    return rows


def current_head():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                   text=True).strip()


def deadline_check(envelope, now=None):
    now = now or datetime.now(timezone.utc)
    if now >= datetime.fromisoformat(envelope['deadline']):
        raise PermissionError('BATCH_WINDOW_EXPIRED')
    if envelope['head'] != current_head():
        import os
        if not os.environ.get('STERBRUST_LIVE11_RECOVERY_FILE'):
            raise PermissionError('BATCH_HEAD_CHANGED_REQUIRES_REVIEW')
        from .source_recovery import allow_old_head
        allow_old_head(envelope)


def phase_scope(snapshot):
    out = deepcopy(snapshot)
    out.pop('source_batch', None)
    settings = out['crawl_settings']
    if settings.get('collection_mode') == 'RESUME':
        settings['collection_mode'] = out['source_input']['mode']
        for key in ('resume_owner_job_id', 'resume_input_sha256'):
            settings.pop(key, None)
    return out


def validate_snapshot(snapshot, now=None):
    envelope = snapshot.get('source_batch')
    if not envelope:
        return
    deadline_check(envelope, now)
    if envelope['matrix_sha256'] != MATRIX_SHA or digest(phase_scope(snapshot)) != envelope['scope_sha256']:
        raise PermissionError('BATCH_IMMUTABLE_SCOPE_CHANGED')
    if envelope['phase'] != snapshot['source_input']['mode']:
        raise PermissionError('BATCH_PHASE_CHANGED')
    if envelope.get('next_snapshot'):
        following = envelope['next_snapshot']
        if digest(following) != envelope['next_sha256']:
            raise PermissionError('BATCH_NEXT_SCOPE_CHANGED')


def prepare(queue, connection, cursor):
    """Caller owns READ ONLY or enqueue transaction and all supplier locks."""
    validate_config(queue.config)
    validate_connection(connection)
    cursor.execute('SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user')
    if cursor.fetchone() != ('rc_runtime', False):
        raise PermissionError('BATCH_ROLE_PREREQUISITE_UNRESOLVED')
    rows = matrix_rows()
    cursor.execute('''SELECT id,code,adapter_name,base_url,enabled,crawl_settings,rate_settings
                      FROM suppliers WHERE code=ANY(%s) ORDER BY id''',
                   ([r['SOURCE'] for r in rows],))
    suppliers = {row[1]: row for row in cursor.fetchall()}
    if set(suppliers) != {r['SOURCE'] for r in rows}:
        raise ValueError('BATCH_CURRENT_SOURCES_MISSING')
    phases = []
    for row in rows:
        code = row['SOURCE']
        sid, *supplier = suppliers[code]
        _, adapter, base, enabled, crawl, rate = supplier
        if adapter != code or normalized_domain(base) != row['APPROVAL_HOST']:
            raise PermissionError('BATCH_SOURCE_HOST_CHANGED')
        if enabled is not True:
            raise PermissionError('BATCH_DISABLED_REQUIRES_EXPLICIT_ENABLE_RESTORE')
        approval = {'host': row['APPROVAL_HOST'], 'reference': row['APPROVAL_REFERENCE']}
        if rate != json.loads(row['RATE_POLICY_JSON']) or (crawl or {}).get('host_policy_approval') != approval:
            raise PermissionError('BATCH_HOST_POLICY_REQUIRES_EXPLICIT_ACTIVATION')
        ceiling = int(row['REFRESH_CEILING'])
        cursor.execute('''WITH known AS (
            SELECT DISTINCT ON (coalesce(canonical_url,source_url)) id,external_id,coalesce(canonical_url,source_url) AS url
            FROM source_products WHERE supplier_id=%s AND active AND external_id_is_stable
            ORDER BY coalesce(canonical_url,source_url),id)
            SELECT id,external_id,url,count(*) OVER() FROM known ORDER BY id LIMIT %s''', (sid, ceiling))
        known = cursor.fetchall()
        if not known or known[0][3] > ceiling:
            raise PermissionError('BATCH_REFRESH_FULL_SCOPE_OUTSIDE_CEILING')
        for mode in ('REFRESH', 'DISCOVERY'):
            is_refresh = mode == 'REFRESH'
            settings = deepcopy(crawl or {})
            settings.update(safety_gate_size=int(row['SAFETY GATE']),
                max_discovery_pages=int(row['PAGES_REFRESH_UNUSED' if is_refresh else 'PAGES_DISCOVERY_PER_JOB']),
                max_run_duration_seconds=int(row['REFRESH_JOB_SECONDS' if is_refresh else 'DISCOVERY_JOB_SECONDS']))
            selected = len(known) if is_refresh else int(row['DISCOVERY_CAP'])
            source = (code, adapter, base, enabled, settings, rate)
            snapshot = build_preview(sid, source, known if is_refresh else [], queue.config, selected, mode)['snapshot']
            if not is_refresh and snapshot['source_input']['seeds'] != json.loads(row['DISCOVERY_SEEDS_JSON']):
                raise PermissionError('BATCH_DISCOVERY_SEEDS_CHANGED')
            phases.append({'supplier_id': sid, 'source': code, 'phase': mode, 'snapshot': snapshot})
    value = {'format': 'live11-batch/v1', 'matrix_sha256': MATRIX_SHA,
             'head': current_head(), 'phases': phases, 'max_active': 2,
             'max_jobs_per_phase': 3, 'window_hours': 96, 'first_source': 'beka_mak',
             'execution_order': list(SOURCE_ORDER)}
    return {'package': value, 'confirmation_sha256': digest(value)}


def preview(queue):
    with queue._connect() as connection:
        connection.read_only = True
        with connection.transaction(), connection.cursor() as cursor:
            return prepare(queue, connection, cursor)


def attach(snapshot, package, batch_id, operator, approved_at, started_at, following=None):
    out = deepcopy(snapshot)
    out['source_batch'] = {'id': batch_id, 'matrix_sha256': MATRIX_SHA,
        'package_sha256': digest(package),
        'head': package['head'], 'operator': operator, 'approved_at': approved_at,
        'started_at': started_at.isoformat(), 'deadline': (started_at + timedelta(hours=96)).isoformat(),
        'phase': snapshot['source_input']['mode'], 'attempt': 1,
        'scope_sha256': digest(snapshot), 'next_snapshot': following,
        'next_sha256': digest(following) if following else None}
    return out


def insert(cursor, supplier_id, snapshot, user_id=None):
    cursor.execute('''INSERT INTO crawl_jobs(supplier_id,trigger_type,crawl_type,status,
        scheduled_for,settings_snapshot,requested_by_user_id)
        VALUES (%s,'manual','full','queued',now(),%s::jsonb,%s) RETURNING id''',
        (supplier_id, json.dumps(snapshot), user_id))
    return cursor.fetchone()[0]


def enqueue(queue, *, confirmation_sha256, batch_id, operator, approved_at, started_at, head, requested_by_user_id=None):
    """One explicit approval; all 11 roots or rollback. Never enable suppliers."""
    if str(UUID(batch_id)) != batch_id or not operator.strip():
        raise ValueError('BATCH_APPROVAL_IDENTITY_REQUIRED')
    approved = datetime.fromisoformat(approved_at)
    started = datetime.fromisoformat(started_at)
    now = datetime.now(timezone.utc)
    if (approved.tzinfo is None or started.tzinfo is None or not approved <= started <= now
            or now - started >= timedelta(hours=96)):
        raise ValueError('BATCH_APPROVAL_TIME')
    with queue._connect() as connection, connection.transaction(), connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
        cursor.execute("SELECT id FROM crawl_jobs WHERE settings_snapshot->'source_batch'->>'id'=%s LIMIT 1", (batch_id,))
        if cursor.fetchone():
            raise PermissionError('BATCH_ALREADY_EXISTS_USE_STATUS_OR_RESUME')
        cursor.execute('SELECT id FROM suppliers WHERE code=ANY(%s) ORDER BY id FOR UPDATE',
                       (list(RUNTIME_SOURCES),))
        cursor.fetchall()
        result = prepare(queue, connection, cursor)
        if result['confirmation_sha256'] != confirmation_sha256 or result['package']['head'] != head:
            raise PermissionError('BATCH_PREVIEW_CHANGED')
        ids = [p['supplier_id'] for p in result['package']['phases']]
        cursor.execute("SELECT id FROM crawl_jobs WHERE supplier_id=ANY(%s) AND status IN ('queued','running')", (ids,))
        if cursor.fetchall():
            raise PermissionError('BATCH_SOURCE_ALREADY_ACTIVE')
        jobs = []
        pairs = list(zip(result['package']['phases'][::2], result['package']['phases'][1::2]))
        for refresh, discovery in sorted(pairs, key=lambda pair: SOURCE_ORDER.index(pair[0]['source'])):
            following = attach(discovery['snapshot'], result['package'], batch_id, operator, approved_at, started)
            snapshot = attach(refresh['snapshot'], result['package'], batch_id, operator, approved_at, started, following)
            jobs.append(insert(cursor, refresh['supplier_id'], snapshot, requested_by_user_id))
        return {'batch_id': batch_id, 'job_ids': jobs, 'phases': 22}


def advance(cursor, supplier_id, snapshot, user_id=None):
    """Same transaction as successful completion. Idempotence is the running CAS."""
    envelope = snapshot.get('source_batch') or {}
    following = envelope.get('next_snapshot')
    if not following:
        return
    try:
        validate_snapshot(snapshot)
        validate_snapshot(following)
    except PermissionError:
        # Terminal parent is retained; report explicitly exposes pending phase.
        return
    insert(cursor, supplier_id, following, user_id)


def resume_envelope(cursor, previous, snapshot, recovery_proof=None):
    envelope = previous.get('source_batch')
    if not envelope:
        return snapshot
    validate_snapshot(previous)
    cursor.execute("""SELECT count(*),bool_or(status='succeeded'),max((settings_snapshot->'source_batch'->>'attempt')::int)
        FROM crawl_jobs WHERE settings_snapshot->'source_batch'->>'id'=%s
        AND supplier_id=%s AND settings_snapshot->'source_batch'->>'phase'=%s""",
        (envelope['id'], previous['source_input']['canary']['supplier_id'], envelope['phase']))
    count, succeeded, latest_attempt = cursor.fetchone()
    if succeeded or latest_attempt != envelope['attempt']:
        raise PermissionError('BATCH_RESUME_LATEST_UNFINISHED_PHASE_ONLY')
    if count >= 3:
        from .source_recovery import post_stop_envelope
        snapshot['source_batch']=post_stop_envelope(previous,count,latest_attempt,recovery_proof)
        snapshot['source_input']=deepcopy(previous['source_input'])
        validate_snapshot(snapshot)
        return snapshot
    snapshot['source_input'] = deepcopy(previous['source_input'])
    snapshot['source_batch'] = {**deepcopy(envelope), 'attempt': count + 1}
    validate_snapshot(snapshot)
    return snapshot


def report(rows):
    """Pure projection for CLI/UI: unknown stays null; no sum of cumulative resume totals."""
    phases = {}
    for row in rows:
        snapshot = row['settings_snapshot']
        envelope = snapshot['source_batch']
        key = (snapshot['supplier_code'], envelope['phase'])
        phases.setdefault(key, []).append(row)
    output = []
    for (source, phase), attempts in sorted(phases.items()):
        latest = max(attempts, key=lambda r: r['id'])
        metadata = latest.get('metadata') or {}
        rt = metadata.get('source_runtime') or {}
        reason = rt.get('termination_reason') or latest.get('error_message')
        state = STATES_RU.get(latest['status'], 'требуется решение оператора')
        if str(reason or '').startswith('OPERATOR') and latest['status'] not in ('queued','running'):
            state = 'остановлено'
        if rt.get('resume_uncertain'):
            state = 'требуется решение оператора'
        discovery = rt.get('discovery_complete') if phase == 'DISCOVERY' else None
        def sum_known(field):
            values = [(r.get('metadata') or {}).get('source_runtime', {}).get(field) for r in attempts]
            return sum(values) if all(type(v) is int for v in values) else None
        output.append({'source': source, 'phase': phase, 'job_id': latest['id'],
            'state': state, 'attempts': len(attempts), 'processed': rt.get('processed'),
            'remaining': rt.get('remaining'), 'sql_receipts': rt.get('sql_committed'),
            'http_by_attempt': [(r.get('metadata') or {}).get('source_runtime', {}).get('request_metrics') for r in attempts],
            'matching_by_attempt': [(r.get('metadata') or {}).get('identity_post_processing') for r in attempts],
            'sql_counts_by_attempt': [(r.get('metadata') or {}).get('source_runtime', {}).get('sql_counts') for r in attempts],
            'price_changes': sum_known('batch_price_changes'), 'stock_changes': sum_known('batch_stock_changes'),
            'discovery_complete': discovery, 'external_catalog_completeness': 'NOT_PROVEN',
            'reason': reason, 'resume_available': rt.get('resume_available', False),
            'operator_action_required': latest['status'] not in ('queued','running','succeeded') or (phase == 'DISCOVERY' and discovery is not True),
            'next_phase_pending': bool(latest['settings_snapshot']['source_batch'].get('next_snapshot')) and (source,'DISCOVERY') not in phases})
    # A phase not yet materialized is still a durable approved scope, visible to operator.
    for item in list(output):
        if item['phase'] == 'REFRESH' and (item['source'], 'DISCOVERY') not in phases:
            output.append({'source': item['source'], 'phase': 'DISCOVERY', 'job_id': None,
                'state': 'ожидает' if item['state'] in ('ожидает','выполняется') else 'требуется решение оператора',
                'reason': 'REFRESH_REQUIRED', 'operator_action_required': item['state'] not in ('ожидает','выполняется'),
                'external_catalog_completeness': 'NOT_PROVEN'})
    return {'phases': sorted(output, key=lambda r: (r['source'], r['phase'])),
            'complete': len(phases) == 22 and all(not r['operator_action_required'] and r['state']=='завершено' for r in output)}


def status(queue, batch_id):
    with queue._connect() as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("""SELECT j.id,j.status,j.settings_snapshot,j.error_message,r.metadata
                FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
                WHERE j.settings_snapshot->'source_batch'->>'id'=%s ORDER BY j.id""", (batch_id,))
            rows = [dict(zip(('id','status','settings_snapshot','error_message','metadata'), row)) for row in cursor.fetchall()]
            cursor.execute("SELECT id FROM audit_events WHERE action=%s AND entity_type='source_batch' AND entity_id=%s LIMIT 1",
                           (BEKA_GATE_ACTION, batch_id))
            gate_released = cursor.fetchone() is not None
    if not rows:
        raise KeyError('BATCH_NOT_FOUND')
    envelope = rows[0]['settings_snapshot']['source_batch']
    approval = {k: envelope[k] for k in ('matrix_sha256','package_sha256','head','operator',
                                         'approved_at','started_at','deadline')}
    summary = report(rows)
    beka_done = any(r['source']=='beka_mak' and r['phase']=='DISCOVERY' and r['state']=='завершено' for r in summary['phases'])
    return {'batch_id': batch_id, 'approval': approval,
            'beka_gate_released': gate_released,
            'beka_gate_confirmation_required': beka_done and not gate_released, **summary}


def confirm_beka_gate(queue, batch_id, receipt_path, receipt_sha256):
    """Record operator-confirmed isolation evidence required by existing plan.

    This is one terminal-receipt control inside the approved package, not a new
    approval every five cards. It does not label operator evidence LIVE-VERIFIED.
    """
    raw = Path(receipt_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise PermissionError('BATCH_BEKA_RECEIPT_CHANGED')
    proof = json.loads(raw)
    keys = {'format','batch_id','head','beka_job_ids','target','outside_before_sha256',
            'outside_after_sha256','mapping_preservation','evidence_class','operator','reference'}
    before = proof.get('outside_before_sha256','')
    if (set(proof) != keys or proof['format'] != 'live11-beka-isolation/v1'
            or proof['batch_id'] != batch_id or proof['target'] != TARGET
            or proof['mapping_preservation'] != 'PASS'
            or proof['evidence_class'] != 'OPERATOR-CONFIRMED'
            or not proof['operator'].strip() or not proof['reference'].strip()
            or len(before) != 64 or any(c not in '0123456789abcdef' for c in before)
            or before != proof['outside_after_sha256']):
        raise PermissionError('BATCH_BEKA_ISOLATION_NOT_CONFIRMED')
    with queue._connect() as connection, connection.transaction(), connection.cursor() as cursor:
        validate_config(queue.config); validate_connection(connection)
        cursor.execute('SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user')
        if cursor.fetchone() != ('rc_runtime', False):
            raise PermissionError('BATCH_ROLE_PREREQUISITE_UNRESOLVED')
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
        cursor.execute("""SELECT id,status,settings_snapshot FROM crawl_jobs
            WHERE settings_snapshot->'source_batch'->>'id'=%s
              AND settings_snapshot->>'supplier_code'='beka_mak' ORDER BY id""", (batch_id,))
        latest = {}
        for jid, state, snapshot in cursor.fetchall():
            latest[snapshot['source_batch']['phase']] = (jid, state, snapshot)
        if set(latest) != {'REFRESH','DISCOVERY'} or any(row[1] != 'succeeded' for row in latest.values()):
            raise PermissionError('BATCH_BEKA_BLOCK_NOT_SUCCEEDED')
        if sorted(proof['beka_job_ids']) != sorted(row[0] for row in latest.values()):
            raise PermissionError('BATCH_BEKA_RECEIPT_JOB_MISMATCH')
        for _, _, snapshot in latest.values():
            validate_snapshot(snapshot)
            if snapshot['source_batch']['head'] != proof['head']:
                raise PermissionError('BATCH_BEKA_RECEIPT_HEAD_MISMATCH')
        from .admin_store import PostgresAdminStore
        PostgresAdminStore._audit(cursor, None, BEKA_GATE_ACTION, 'source_batch', batch_id,
            None, {**proof,'receipt_sha256':receipt_sha256}, 'Existing LIVE11 terminal isolation gate', batch_id)
    return {'batch_id':batch_id,'beka_gate_released':True,'evidence_class':'OPERATOR-CONFIRMED'}


def resume_batch(queue, batch_id):
    """Explicit operator action; never used by scheduler/tick or for UNCERTAIN."""
    with queue._connect() as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("""SELECT DISTINCT ON (j.supplier_id,j.settings_snapshot->'source_batch'->>'phase')
                j.supplier_id,j.id,j.status,r.metadata FROM crawl_jobs j
                LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
                WHERE j.settings_snapshot->'source_batch'->>'id'=%s
                ORDER BY j.supplier_id,j.settings_snapshot->'source_batch'->>'phase',j.id DESC""", (batch_id,))
            rows = cursor.fetchall()
    if not rows:
        raise KeyError('BATCH_NOT_FOUND')
    result = []
    for sid, jid, state, metadata in rows:
        rt = (metadata or {}).get('source_runtime') or {}
        if state not in ('partial','failed','cancelled','stale_failed'):
            continue
        if not resume_is_safe(rt):
            result.append({'parent_job_id': jid, 'status': 'OPERATOR_DECISION_REQUIRED'})
            continue
        try:
            child = queue.enqueue(sid, trigger_type='manual', crawl_type='full',
                                  source_mode='RESUME', resume_job_id=jid)
            result.append({'parent_job_id': jid, 'job_id': child.job_id, 'status': child.status})
        except (ValueError, PermissionError) as error:
            result.append({'parent_job_id': jid, 'status': 'OPERATOR_DECISION_REQUIRED', 'reason': str(error)})
    return result


def resume_is_safe(runtime):
    # Completion can fail to insert D after SQL+matching already succeeded.
    # A technical child reconciles COMMITTED without GET/ingest and retries only
    # control completion. Unknown/failed host results never use this exception.
    return not runtime.get('resume_uncertain') and (
        runtime.get('resume_available') is True or
        (runtime.get('status') == 'succeeded' and runtime.get('remaining') == 0))
