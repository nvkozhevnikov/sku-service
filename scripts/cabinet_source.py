"""Per-source UI bindings to existing queue; no crawler, queue or schema here."""
from datetime import datetime, timezone
import json
import os

from cabinet_batch_read import target_config, checked_role, require_write_session
from universal_supplier.control_plane import source_batch
from universal_supplier.control_plane.queue import PostgresJobQueue, normalized_domain
from universal_supplier.control_plane.source_runtime import runtime_enabled

FLAGS = ('STERBRUST_SOURCE_RUNTIME_ENABLED', 'STERBRUST_ALLOW_SUPPLIER_HTTP',
         'STERBRUST_ALLOW_SOURCE_WRITES')
TERMINAL = {'partial', 'failed', 'cancelled', 'stale_failed'}


def qa_marker(value):
    if isinstance(value, dict):
        return any((key in {'synthetic', 'is_synthetic'} and item is True) or qa_marker(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(qa_marker(item) for item in value)
    return isinstance(value, str) and ('SYNTHETIC' in value.upper() or 'NOT_HTTP' in value.upper())


def matrix_source(code):
    rows = {row['SOURCE']: row for row in source_batch.matrix_rows()}
    if code not in rows:
        raise ValueError('SOURCE_NAMESPACE_NOT_ALLOWED')
    return rows[code]


def validate_request(code, mode, limit=None, job_id=None):
    matrix = matrix_source(code)
    if mode not in {'REFRESH', 'DISCOVERY', 'RESUME'}:
        raise ValueError('SOURCE_MODE')
    if mode == 'RESUME':
        if limit is not None:
            raise ValueError('RESUME_SCOPE_CANNOT_BE_REPLACED')
        if job_id is not None and (type(job_id) is not int or job_id < 1):
            raise ValueError('RESUME_PARENT_REQUIRED')
    elif limit is not None:
        cap = int(matrix['REFRESH_CEILING' if mode == 'REFRESH' else 'DISCOVERY_CAP'])
        if type(limit) is not int or not 1 <= limit <= cap:
            raise ValueError('SOURCE_CAP_EXCEEDS_LIVE11_MATRIX')
    return matrix


def load_source(queue, code, job_id=None):
    """Only SELECT, source is looked up in target DB; never use historical IDs."""
    with queue._connect() as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,code,adapter_name,base_url,enabled,crawl_settings,rate_settings FROM suppliers WHERE code=%s', (code,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError('SOURCE_NOT_PRESENT_IN_TARGET')
            source = dict(zip(('id','code','adapter','base_url','enabled','crawl','rate'), row))
            cursor.execute('''SELECT j.id,j.status,j.settings_snapshot,j.error_message,j.crawl_run_id,
                              j.created_at,j.started_at,j.finished_at,r.metadata
                              FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
                              WHERE j.supplier_id=%s ORDER BY (j.id=%s) DESC,j.id DESC LIMIT 50''', (source['id'], job_id or 0))
            jobs = [project_job(r) for r in cursor.fetchall()]
    return source, jobs


def project_job(row):
    jid, state, snapshot, error, run_id, created, started, finished, metadata = row
    snapshot, metadata = snapshot or {}, metadata or {}
    runtime = metadata.get('source_runtime') or {}
    provenance = 'SYNTHETIC_NOT_LIVE' if qa_marker([snapshot, metadata]) else 'SAVED_METADATA_REQUIRES_EVIDENCE'
    return {'id': jid, 'status': state, 'state': source_batch.STATES_RU.get(state, state), 'run_id': run_id, 'created_at': created,
            'provenance': provenance,
            'started_at': started, 'finished_at': finished,
            'mode': (snapshot.get('crawl_settings') or {}).get('collection_mode'),
            'batch_id': (snapshot.get('source_batch') or {}).get('id'),
            'error': error or metadata.get('failure') or runtime.get('termination_reason'),
            'http_progress': metadata.get('progress'), 'http': runtime.get('request_metrics'),
            'processed': runtime.get('processed'), 'remaining': runtime.get('remaining'),
            'sql_receipts': runtime.get('sql_committed'), 'sql_counts': runtime.get('sql_counts'),
            'price_changes': runtime.get('batch_price_changes'), 'stock_changes': runtime.get('batch_stock_changes'),
            'matching': metadata.get('identity_post_processing'),
            'resume_available': state in TERMINAL and (source_batch.resume_is_safe(runtime) if snapshot.get('source_batch')
                else runtime.get('resume_available') is True and not runtime.get('resume_uncertain')),
            'uncertain': runtime.get('resume_uncertain'),
            'owner_job_id': runtime.get('owner_job_id'), 'input_sha256': runtime.get('input_sha256'),
            'discovery_complete': runtime.get('discovery_complete')}


def source_guard(source, matrix, *, policy=True):
    if source['adapter'] != source['code'] or normalized_domain(source['base_url']) != matrix['APPROVAL_HOST']:
        raise PermissionError('BATCH_SOURCE_HOST_CHANGED')
    if not source['enabled']:
        raise PermissionError('BATCH_DISABLED_REQUIRES_EXPLICIT_ENABLE_RESTORE')
    if policy and (source['rate'] != json.loads(matrix['RATE_POLICY_JSON']) or
            (source['crawl'] or {}).get('host_policy_approval') != {
                'host': matrix['APPROVAL_HOST'], 'reference': matrix['APPROVAL_REFERENCE']}):
        raise PermissionError('BATCH_HOST_POLICY_REQUIRES_EXPLICIT_ACTIVATION')


def bounded_preview(queue, source, matrix, mode, limit):
    preview = queue.preview_source_canary(source['id'], limit, mode)
    scope = preview['snapshot']['source_input']['canary']
    refresh = mode == 'REFRESH'
    if ((not refresh and scope['max_discovery_pages'] > int(matrix['PAGES_DISCOVERY_PER_JOB'])) or
            scope['max_run_duration_seconds'] > int(matrix['REFRESH_JOB_SECONDS' if refresh else 'DISCOVERY_JOB_SECONDS']) or
            scope['safety_gate_size'] > int(matrix['SAFETY GATE'])):
        raise PermissionError('SOURCE_BUDGET_EXCEEDS_LIVE11_MATRIX')
    return preview


def inspect_source(code, mode='REFRESH', limit=None, job_id=None):
    matrix = validate_request(code, mode, limit, job_id)
    os.environ['PGCONNECT_TIMEOUT'] = '2'
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=5000'
    config = target_config()
    queue = PostgresJobQueue(config)
    result = {'code': code, 'mode': mode, 'limit': limit, 'job_id': job_id,
              'matrix': matrix, 'head': source_batch.current_head(), 'connected': False,
              'source': None, 'jobs': [], 'preview': None, 'resume_verified': False,
              'ready': False, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'target': {'host': config.host, 'port': config.port, 'database': config.dbname, 'role': config.user},
              'flags': {flag: os.environ.get(flag) == 'YES' for flag in FLAGS}}
    try:
        role, superuser = checked_role(queue)
        result.update(connected=True, role=role, superuser=superuser)
        source, jobs = load_source(queue, code, job_id)
        result.update(source=source, jobs=jobs)
        if job_id is not None and mode != 'RESUME' and limit is None:
            if not any(j['id'] == job_id for j in jobs):
                raise ValueError('SOURCE_JOB_NOT_FOUND')
            result['reason'] = 'SOURCE_JOB_STATUS'
            return result
        if (role, superuser) != ('rc_runtime', False):
            raise PermissionError('BATCH_ROLE_PREREQUISITE_UNRESOLVED')
        source_guard(source, matrix)
        if not runtime_enabled():
            raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
        if mode == 'RESUME':
            parent = next((j for j in jobs if j['id'] == job_id), None)
            if not parent:
                raise ValueError('RESUME_SELECT_EXISTING_PARENT')
            if not parent['resume_available']:
                raise PermissionError('UNCERTAIN_OR_FAILED_RESUME')
            queue.source_resume_status(job_id)
            result.update(resume_verified=True, ready=True, reason='SOURCE_RESUME_VERIFIED')
        elif limit is None:
            result['reason'] = 'SOURCE_EXPLICIT_SCOPE_REQUIRED'
        else:
            result.update(preview=bounded_preview(queue, source, matrix, mode, limit),
                          ready=True, reason='SOURCE_PREVIEW_READY')
    except (PermissionError, ValueError, KeyError) as error:
        result['reason'] = str(error).strip("'")
    except Exception as error:
        state = getattr(error, 'sqlstate', None)
        result['reason'] = 'LIVE11_TARGET_AUTH_FAILED' if state in {'28000', '28P01'} else (
            'LIVE11_TARGET_SCHEMA_UNVERIFIED' if result['connected'] else 'LIVE11_TARGET_CONNECTION_FAILED')
    return result


def source_command(action, values):
    require_write_session()
    if action not in {'source-enqueue', 'source-resume', 'source-stop'}:
        raise ValueError('SOURCE_ACTION_NOT_ALLOWED')
    code = values['source']
    mode = values.get('mode', 'RESUME' if action == 'source-resume' else 'REFRESH')
    limit, job_id = values.get('limit'), values.get('job_id')
    matrix = validate_request(code, mode, limit, job_id)
    os.environ['PGCONNECT_TIMEOUT'] = '2'
    queue = PostgresJobQueue(target_config())
    if checked_role(queue) != ('rc_runtime', False):
        raise PermissionError('BATCH_ROLE_PREREQUISITE_UNRESOLVED')
    source, jobs = load_source(queue, code, job_id)
    if action == 'source-stop':
        parent = next((j for j in jobs if j['id'] == job_id), None)
        if not parent or parent['status'] != 'running':
            raise PermissionError('NO_ACTIVE_SOURCE_RUNTIME_JOB')
        queue.stop_source(source['id'], job_id)
        return {'source': code, 'job_id': job_id, 'status': 'STOP_REQUESTED'}
    source_guard(source, matrix)
    if action == 'source-resume':
        if mode != 'RESUME':
            raise ValueError('RESUME_SCOPE_CANNOT_BE_REPLACED')
        parent = next((j for j in jobs if j['id'] == job_id), None)
        if not parent or not parent['resume_available']:
            raise PermissionError('UNCERTAIN_OR_FAILED_RESUME')
        queue.source_resume_status(job_id)
        job = queue.enqueue(source['id'], trigger_type='manual', crawl_type='full',
                            source_mode='RESUME', resume_job_id=job_id)
    else:
        if mode not in {'REFRESH', 'DISCOVERY'} or limit is None or job_id is not None:
            raise ValueError('SOURCE_EXPLICIT_SCOPE_REQUIRED')
        preview = bounded_preview(queue, source, matrix, mode, limit)
        if preview['confirmation_sha256'] != values.get('confirmation_sha256'):
            raise PermissionError('SOURCE_PREVIEW_CHANGED')
        job = queue.enqueue(source['id'], trigger_type='manual', crawl_type='full', source_mode=mode,
                            source_limit=limit, source_preview_sha256=values['confirmation_sha256'])
    return {'source': code, 'job_id': job.job_id, 'status': job.status}
