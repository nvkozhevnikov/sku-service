"""One-use runner. Inspect is SELECT-only; run needs the exact new approval."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from universal_supplier.control_plane import source_recovery as recovery
from universal_supplier.control_plane.source_autonomy import Controller, policy, keep_awake
from universal_supplier.control_plane.source_hourly import collect, atomic_text
from cabinet_batch_read import target_config


def inspect(path, sha):
    recovery.require(recovery.file_sha(path) == sha, 'AUTONOMOUS_APPROVAL_SHA_CHANGED')
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    p=policy(value)
    from universal_supplier.control_plane.source_canary import TARGET
    for volume in (ROOT,Path(TARGET['pgdata'])):
        recovery.require(shutil.disk_usage(volume).free>=p['minimum_free_bytes'],'AUTONOMOUS_DISK_LOW')
    recovery.require(value['compatibility'] == recovery.compatibility(ROOT,
        value['compatibility']['base_head'], value['compatibility']['base_engine']), 'AUTONOMOUS_HEAD_CHANGED')
    for item in value['artifacts'].values():
        recovery.require(recovery.file_sha(item['path']) == item['sha256'], 'RECOVERY_EVIDENCE_CHANGED')
    state = collect(target_config(), value, worker_state='NOT_STARTED')
    recovery.require(not state['active_jobs'] and state['settings_restored_exact'], 'AUTONOMOUS_PREFLIGHT_NOT_IDLE_BASELINE')
    return value, state


def main(argv=None):
    started = datetime.now(timezone.utc); started_mono=time.monotonic()
    parser = argparse.ArgumentParser(description='LIVE11 one-shot24h; no scheduler/LLM/implicit restart')
    parser.add_argument('action', choices=['inspect', 'run'])
    parser.add_argument('--approval', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--confirm')
    args = parser.parse_args(argv)
    os.environ['SOURCE_RUNNER_EVIDENCE_ROOT']=str(ROOT/'reports/source-runner-evidence')
    if args.action == 'inspect':
        value, state = inspect(args.approval, args.sha256)
        print(json.dumps({'head': value['compatibility']['head'], 'sha256': args.sha256,
            'committed': state['committed'], 'unique_products': state['unique_products'],
            'original1282_exact': True, 'active_jobs': [], 'settings_restored_exact': True,
            'HTTP': 0, 'enqueue': 0, 'ingest': 0, 'matching': 0,
            'execution': 'PENDING_EXACT_OPERATOR_CONFIRMATION'}))
        return 0
    recovery.require(args.confirm == args.sha256, 'RECOVERY_OPERATOR_CONFIRMATION_REQUIRED')
    os.environ.update({recovery.ENV + '_FILE': str(args.approval.resolve()),
        recovery.ENV + '_SHA256': args.sha256, recovery.ENV + '_CONFIRM': args.confirm})
    manifest, sha = recovery.approval()
    folder = ROOT / 'reports/autonomous-runs' / sha
    folder.mkdir(parents=True, exist_ok=True)
    # One known log file, no credential arguments or printed DSNs.
    with (folder / 'runner.log').open('a', encoding='utf-8', buffering=1) as log:
        oldout, olderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        control = Controller(target_config(), manifest, sha, folder, now=started, started_monotonic=started_mono)
        claimed = False; result = 0
        try:
            with keep_awake():
                try:
                    control.start(); claimed = True
                    from live11_recovery_session import run
                    from universal_supplier.control_plane.queue import PostgresJobQueue
                    run(PostgresJobQueue(target_config()), control=control)
                except BaseException as error:
                    reason=str(error) if str(error).startswith(('AUTONOMOUS_','RECOVERY_','BATCH_','OPERATOR_')) else type(error).__name__
                    control.fail(reason);raise
                finally:
                    if control.claimed: control.finish()
        except BaseException as error:
            result = 1
            reason = str(error) if str(error).startswith(('AUTONOMOUS_', 'RECOVERY_', 'BATCH_', 'OPERATOR_')) else type(error).__name__
            atomic_text(folder / 'EXIT_ERROR.json', json.dumps({'at': datetime.now(timezone.utc).isoformat(),
                'reason': reason, 'automatic_restart': False}))
            print(reason)
        finally:
            sys.stdout, sys.stderr = oldout, olderr
        return result


if __name__ == '__main__':
    raise SystemExit(main())
