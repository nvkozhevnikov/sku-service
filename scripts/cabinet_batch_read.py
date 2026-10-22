"""Cabinet adapter to existing batch commands; writes require explicit session admission."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.control_plane import source_batch, source_canary
from universal_supplier.control_plane.queue import PostgresJobQueue
from universal_supplier.postgres import PostgresConfig


def target_config():
    return PostgresConfig('127.0.0.1', 55461, 'rc_verify_vi_20261019', 'rc_owner', None, 'disable')


def require_write_session():
    if os.environ.get('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM') != 'rc_verify_vi_20261019':
        raise PermissionError('LIVE11_WRITE_SESSION_NOT_APPROVED')


def checked_role(queue):
    source_canary.validate_config(queue.config)
    with queue._connect() as connection:
        connection.read_only = True
        source_canary.validate_connection(connection)
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user')
            return cursor.fetchone()


def read(action, batch_id=None):
    if action not in {'preview', 'status'}:
        raise ValueError('READ_ONLY_ACTION_REQUIRED')
    if action == 'status':
        batch_id = str(UUID(batch_id))
    # This is the exact existing guarded target, never the serving panel's DB.
    config = target_config()
    os.environ['PGCONNECT_TIMEOUT'] = '2'
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=5000'
    queue = PostgresJobQueue(config)
    result = {'target': {'host': config.host, 'port': config.port, 'database': config.dbname,
                         'role': config.user}, 'matrix': source_batch.matrix_rows(),
              'head': source_batch.current_head(), 'connected': False, 'payload': None,
              'launch_allowed': False}
    try:
        source_canary.validate_config(config)
        with queue._connect() as connection:
            connection.read_only = True
            source_canary.validate_connection(connection)
            with connection.cursor() as cursor:
                cursor.execute('SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user')
                role, superuser = cursor.fetchone()
        result.update(connected=True, role=role, superuser=superuser)
        if action == 'status':
            result['payload'] = queue.source_batch_status(batch_id)
        else:
            result['payload'] = queue.preview_source_batch()
        result['reason'] = 'LIVE11_EXPLICIT_APPROVAL_AND_SETUP_REQUIRED'
    except (PermissionError, ValueError, KeyError) as error:
        result['reason'] = str(error).strip("'")
    except Exception:
        result['reason'] = 'LIVE11_TARGET_NOT_CONNECTED_OR_IDENTITY_UNVERIFIED'
    return result


def command(action, values):
    require_write_session()
    if action not in {'enqueue', 'stop', 'resume', 'confirm-beka'}:
        raise ValueError('UNSUPPORTED_BATCH_ACTION')
    config = target_config()
    source_canary.validate_config(config)
    os.environ['PGCONNECT_TIMEOUT'] = '2'
    queue = PostgresJobQueue(config)
    # Validate the exact cluster/role even for STOP/RESUME; never inherit RC55449.
    if checked_role(queue) != ('rc_owner', False):
        raise PermissionError('BATCH_ROLE_PREREQUISITE_UNRESOLVED')
    if action == 'enqueue':
        # Existing backend repeats preview under locks, checks exact digest/head,
        # enabled, approved policy, immutable scopes and T0 before any inserts.
        allowed = {'confirmation_sha256', 'batch_id', 'operator', 'approved_at', 'started_at', 'head'}
        if set(values) != allowed:
            raise ValueError('EXACT_EXISTING_APPROVAL_FIELDS_REQUIRED')
        return queue.enqueue_source_batch(**values)
    batch_id = str(UUID(values['batch_id']))
    if action == 'confirm-beka':
        receipt = values['receipt_json'].encode('utf-8')
        if hashlib.sha256(receipt).hexdigest() != values['receipt_sha256']:
            raise ValueError('BATCH_BEKA_RECEIPT_SHA_MISMATCH')
        directory = Path(__file__).resolve().parents[1] / 'reports/cabinet-receipts'
        directory.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4
        path = directory / f'{uuid4()}.json'
        path.write_bytes(receipt)
        return source_batch.confirm_beka_gate(queue, batch_id, path, values['receipt_sha256'])
    if action == 'stop':
        queue.stop_source_batch(batch_id)
        return queue.source_batch_status(batch_id)
    return source_batch.resume_batch(queue, batch_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preview', 'status', 'enqueue', 'stop', 'resume', 'confirm-beka',
                                         'source', 'source-enqueue', 'source-stop', 'source-resume'])
    parser.add_argument('--batch-id')
    parser.add_argument('--source')
    parser.add_argument('--mode', choices=['REFRESH', 'DISCOVERY', 'RESUME'], default='REFRESH')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--job-id', type=int)
    args = parser.parse_args()
    if args.action == 'source':
        from cabinet_source import inspect_source
        try:
            result = inspect_source(args.source, args.mode, args.limit, args.job_id)
        except (PermissionError, ValueError) as error:
            result = {'code': args.source, 'mode': args.mode, 'limit': args.limit,
                      'connected': False, 'ready': False, 'source': None, 'jobs': [],
                      'preview': None, 'reason': str(error)}
    elif args.action in {'preview', 'status'}:
        result = read(args.action, args.batch_id)
    else:
        try:
            if args.action.startswith('source-'):
                from cabinet_source import source_command
                result = {'result': source_command(args.action, json.load(sys.stdin))}
            else:
                result = {'result': command(args.action, json.load(sys.stdin))}
        except (PermissionError, ValueError, KeyError) as error:
            result = {'error': str(error).strip("'")}
        except Exception:
            result = {'error': 'LIVE11_TARGET_NOT_CONNECTED_OR_IDENTITY_UNVERIFIED'}
    print(json.dumps(result, ensure_ascii=False, default=str))
