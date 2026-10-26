"""Bounded, one-use Windows session around the existing LIVE11 worker."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ctypes
import hashlib
import json
import os
import shutil
import threading
import time
from .source_hourly import atomic_text, collect, save_report

ACTIVE = None
SESSION_ENV = 'STERBRUST_LIVE11_AUTONOMOUS_SESSION'


def policy(value):
    from .source_recovery import file_sha, require
    ref = value.get('continuation', {}).get('autonomous')
    require(ref and file_sha(ref['path']) == ref['sha256'], 'AUTONOMOUS_POLICY_REQUIRED')
    p = json.loads(Path(ref['path']).read_text(encoding='utf-8'))
    require(p.get('format') == 'live11-one-shot/v1' and p.get('batch_id') == value['batch_id'] and
        p.get('previous_approval_sha256') == '2ba5bfd133ae54fb468567655622c784980d5db848bc3318846582eb5a1bca12' and
        p.get('original_started_at') == value['started_at'] and p.get('original_deadline') == value['deadline'] and
        p.get('matrix_sha256') == value['matrix_sha256'] and p.get('scope_hashes') == value['scope_hashes'] and
        p.get('input_hashes') == value['input_hashes'] and p.get('extra_refresh_parents') == list(range(119, 128)) and
        p.get('refresh_max_attempt') == 12 and p.get('discovery_max_attempt') == 4 and
        p.get('max_runtime_seconds') == 86400 and p.get('shutdown_reserve_seconds') == 300 and
        p.get('report_interval_seconds') == 3600 and p.get('minimum_free_bytes') == 5 * 1024**3 and
        p.get('launch_not_after') == '2026-10-27T11:27:36+00:00' and
        p.get('execution_not_after') == '2026-10-28T11:27:36+00:00', 'AUTONOMOUS_POLICY_CHANGED')
    return p


def context(value, *, required=False):
    from .source_recovery import require, file_sha, ENV
    p = policy(value); path = os.environ.get(SESSION_ENV)
    if not path:
        require(not required, 'AUTONOMOUS_ONE_SHOT_LAUNCH_REQUIRED')
        return None
    require(file_sha(path) == os.environ.get(SESSION_ENV + '_SHA256'), 'AUTONOMOUS_SESSION_CHANGED')
    s = json.loads(Path(path).read_text(encoding='utf-8'))
    start = datetime.fromisoformat(s['started_at']); end = datetime.fromisoformat(s['hard_until'])
    require(s['approval_sha256'] == os.environ.get(ENV + '_SHA256') and s['head'] == value['compatibility']['head'] and
        s['batch_id'] == value['batch_id'] and start.tzinfo is not None and
        start <= datetime.fromisoformat(p['launch_not_after']) and
        end == min(start + timedelta(seconds=p['max_runtime_seconds']), datetime.fromisoformat(p['execution_not_after'])) and
        datetime.fromisoformat(s['work_until']) == end - timedelta(seconds=p['shutdown_reserve_seconds']),
        'AUTONOMOUS_SESSION_NOT_APPROVED')
    return s


def execution_deadline(value):
    p = policy(value); s = context(value)
    # Before launch this is only a launch deadline, never a running lease.
    return datetime.fromisoformat(s['work_until'] if s else p['launch_not_after'])


@contextmanager
def keep_awake(api=None):
    """Thread-scoped idle-sleep request; screen, power plan and security remain unchanged."""
    if api is None:
        if os.name != 'nt': raise RuntimeError('WINDOWS_REQUIRED')
        api = ctypes.windll.kernel32.SetThreadExecutionState
        api.argtypes = [ctypes.c_uint32]; api.restype = ctypes.c_uint32
    if not api(0x80000001): raise RuntimeError('WINDOWS_KEEP_AWAKE_FAILED')
    try: yield
    finally:
        if not api(0x80000000): raise RuntimeError('WINDOWS_KEEP_AWAKE_RELEASE_FAILED')


class Controller:
    def __init__(self, config, manifest, sha, folder, *, now=None, monotonic=time.monotonic,
                 disk_usage=shutil.disk_usage, snapshot=collect, utcnow=None, started_monotonic=None):
        self.config = config; self.manifest = manifest; self.sha = sha; self.folder = Path(folder)
        self.p = policy(manifest); self.monotonic = monotonic; self.started_mono = monotonic() if started_monotonic is None else started_monotonic
        self.started = now or datetime.now(timezone.utc)
        if self.started > datetime.fromisoformat(self.p['launch_not_after']): raise PermissionError('AUTONOMOUS_LAUNCH_WINDOW_EXPIRED')
        self.hard = min(self.started + timedelta(seconds=self.p['max_runtime_seconds']), datetime.fromisoformat(self.p['execution_not_after']))
        self.work = self.hard - timedelta(seconds=self.p['shutdown_reserve_seconds'])
        self.work_seconds = (self.work - self.started).total_seconds()
        self.disk_usage = disk_usage; self.snapshot = snapshot; self.stop = threading.Event(); self.done = threading.Event()
        self.lock = threading.RLock(); self.failure = None; self.previous = None; self.index = 0; self.thread = None
        self.claimed = False; self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self.last_disk = float('-inf')

    def claim(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        s = {'batch_id': self.manifest['batch_id'], 'approval_sha256': self.sha,
             'head': self.manifest['compatibility']['head'], 'started_at': self.started.isoformat(),
             'work_until': self.work.isoformat(), 'hard_until': self.hard.isoformat(), 'pid': os.getpid()}
        path = self.folder / 'CLAIM.json'
        # A failed/crashed run consumes this exact approval as well. No restart.
        with path.open('x', encoding='utf-8') as f:
            json.dump(s, f); f.flush(); os.fsync(f.fileno())
        self.claimed = True
        os.environ[SESSION_ENV] = str(path)
        os.environ[SESSION_ENV + '_SHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_text(self.folder / 'RESTORATION_PENDING.json', json.dumps({'batch_id': s['batch_id'],
            'baseline': self.manifest['settings_before'], 'note': 'Verify SELECT after normal exit or crash; no automatic restart.'}, default=str))
        return s

    def fail(self, reason):
        with self.lock:
            if not self.failure: self.failure = reason
            self.stop.set()

    def reason(self):
        with self.lock:
            if self.failure: return self.failure
            if self.monotonic() - self.started_mono >= self.work_seconds or self.utcnow() >= self.work:
                self.fail('AUTONOMOUS_TIME_LIMIT'); return self.failure
            if (self.folder / 'STOP_REQUESTED').exists():
                self.fail('OPERATOR_STOP'); return self.failure
            if self.monotonic() - self.last_disk >= 5:
                self.last_disk = self.monotonic()
                try:
                    # Both evidence/PGDATA volumes; no disk cleanup or backup.
                    from .source_canary import TARGET
                    for path in (self.folder, Path(TARGET['pgdata'])):
                        if self.disk_usage(path).free < self.p['minimum_free_bytes']:
                            self.fail('AUTONOMOUS_DISK_LOW'); break
                except OSError: self.fail('AUTONOMOUS_DISK_CHECK_FAILED')
            return self.failure

    def check(self):
        reason = self.reason()
        if reason: raise RuntimeError(reason)

    def report(self, state):
        try:
            value = self.snapshot(self.config, self.manifest, previous=self.previous, worker_state=state)
            save_report(self.folder / 'hourly', value, self.index)
            self.previous = value; self.index += 1
            return value
        except Exception as error:
            reason = str(error) if str(error).startswith(('HOURLY_', 'RECOVERY_')) else type(error).__name__
            failure = {'at': datetime.now(timezone.utc).isoformat(), 'postgres': 'UNVERIFIED',
                'worker': state, 'error': reason, 'sources': [], 'original1282_exact': 'UNVERIFIED'}
            save_report(self.folder / 'hourly', failure, self.index); self.index += 1
            self.fail('AUTONOMOUS_REPORT_OR_SQL_FAILED'); raise

    def start(self):
        global ACTIVE
        if ACTIVE is not None: raise RuntimeError('AUTONOMOUS_SESSION_ALREADY_ACTIVE')
        self.claim(); context(self.manifest, required=True); self.check()
        initial = self.report('PREFLIGHT')
        if initial['active_jobs'] or not initial['settings_restored_exact']:
            raise RuntimeError('AUTONOMOUS_PREFLIGHT_NOT_IDLE_BASELINE')
        ACTIVE = self
        def monitor():
            next_report = self.monotonic() + self.p['report_interval_seconds']
            next_heartbeat = self.monotonic()
            next_health = self.monotonic()
            while not self.done.wait(1):
                try:
                    self.check()
                    if self.monotonic() >= next_health:
                        import psycopg
                        from .source_canary import validate_connection
                        with psycopg.connect(**self.config.kwargs(), connect_timeout=5, autocommit=True,
                                options='-c default_transaction_read_only=on -c statement_timeout=10000') as c:
                            validate_connection(c)
                            if c.execute("SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit')").fetchone() != ('on','on','on'):
                                raise RuntimeError('AUTONOMOUS_POSTGRES_DURABILITY_REQUIRED')
                        next_health = self.monotonic() + 30
                    if self.monotonic() >= next_heartbeat:
                        atomic_text(self.folder / 'heartbeat.json', json.dumps({'at': datetime.now(timezone.utc).isoformat(),
                            'worker': 'RUNNING', 'stop_reason': self.failure, 'work_until': self.work.isoformat()}))
                        next_heartbeat = self.monotonic() + 10
                    if self.monotonic() >= next_report:
                        self.report('RUNNING'); next_report += self.p['report_interval_seconds']
                except Exception:
                    self.fail(self.failure or 'AUTONOMOUS_MONITOR_FAILURE'); return
        self.thread = threading.Thread(target=monitor, name='live11-local-report', daemon=True); self.thread.start()

    def finish(self):
        global ACTIVE
        self.done.set()
        if self.thread: self.thread.join(timeout=45)
        if self.thread and self.thread.is_alive():
            ACTIVE = None
            raise RuntimeError('AUTONOMOUS_REPORT_THREAD_DID_NOT_STOP')
        try:
            result = self.report('STOPPED')
            restored = result['settings_restored_exact'] and not result['active_jobs']
            atomic_text(self.folder / 'FINAL.json', json.dumps({'at': result['at'], 'settings_restored': restored,
                'active_jobs': result['active_jobs'], 'stop_reason': self.failure, 'no_automatic_restart': True}))
            if restored: (self.folder / 'RESTORATION_PENDING.json').unlink(missing_ok=True)
        finally: ACTIVE = None


def stop_reason():
    return ACTIVE.reason() if ACTIVE is not None else None


def require_active(value):
    """A confirmed manifest alone never creates an unbounded background queue."""
    if not value.get('continuation',{}).get('autonomous'):return
    from .source_recovery import require,ENV
    context(value,required=True)
    require(ACTIVE is not None and ACTIVE.sha==os.environ.get(ENV+'_SHA256') and
        ACTIVE.manifest['batch_id']==value['batch_id'],'AUTONOMOUS_ONE_SHOT_LAUNCH_REQUIRED')
    ACTIVE.check()
