"""Native claim SQL after frozen expiry; saved models, no HTTP/PostgreSQL."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from test_source_batch import Database, start
from test_source_canary import offline_env
from test_live11_autonomous import controller
from universal_supplier.control_plane import source_autonomy as auto, source_recovery as recovery


def expired_queue(monkeypatch):
    monkeypatch.delenv(recovery.ENV + '_FILE', raising=False)
    db = Database(monkeypatch, counts=2)
    start(db, batch_id='3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d')
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for job in db.jobs:
        job['settings_snapshot']['source_batch']['deadline'] = old
    return db


def active_window(db, tmp_path, monkeypatch):
    control, value, clock = controller(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, 'approval', lambda: (value, 'b' * 64))
    monkeypatch.setenv(recovery.ENV + '_FILE', 'offline-exact-approval')
    control.claim()
    monkeypatch.setattr(auto, 'ACTIVE', control)
    # Envelope/HEAD compatibility is separately exercised with saved real
    # envelopes in test_live11_r10_dispatch. Keep native queue validation here.
    monkeypatch.setattr(recovery, 'approved_execution_deadline', lambda envelope: control.work)
    # The controller runs on a frozen 2026-10-26 clock, while the fake queue
    # compares the window with the real time: keep the window open relative to
    # now, otherwise the test silently expires after that date.
    control.work = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(recovery, 'execution_deadline', lambda envelope: control.work)
    return control, clock


def test_native_claim_honors_active_extension_after_original_deadline(tmp_path, monkeypatch):
    db = expired_queue(monkeypatch)
    before = [deepcopy(j['settings_snapshot']) for j in db.jobs]
    control, _ = active_window(db, tmp_path, monkeypatch)
    job = db.queue.claim_next()
    assert job and job.supplier_code == 'beka_mak'
    assert sum(j['status'] == 'running' for j in db.jobs) == 1
    assert all(j['status'] in ('queued', 'running') for j in db.jobs)
    assert [j['settings_snapshot'] for j in db.jobs] == before
    expiry = next(args for sql, args in db.sql if 'BATCH_WINDOW_EXPIRED' in sql)
    assert expiry == (control.manifest['batch_id'], control.work)


def test_expired_unapproved_batch_still_cancelled(monkeypatch):
    db = expired_queue(monkeypatch)
    assert db.queue.claim_next() is None
    assert all(j['status'] == 'cancelled' and j['error_message'] == 'BATCH_WINDOW_EXPIRED' for j in db.jobs)


def test_extension_does_not_cover_another_batch(tmp_path, monkeypatch):
    db = expired_queue(monkeypatch)
    active_window(db, tmp_path, monkeypatch)
    foreign = db.jobs[-1]
    foreign['settings_snapshot']['source_batch']['id'] = 'other-batch'
    assert db.queue.claim_next() is not None
    assert foreign['status'] == 'cancelled' and foreign['error_message'] == 'BATCH_WINDOW_EXPIRED'


@pytest.mark.parametrize('failure', ['inactive', 'time', 'sha'])
def test_critical_approval_or_controller_failure_precedes_queue_mutation(tmp_path, monkeypatch, failure):
    db = expired_queue(monkeypatch)
    control, clock = active_window(db, tmp_path, monkeypatch)
    before = deepcopy(db.jobs); db.sql.clear()
    if failure == 'inactive':
        monkeypatch.setattr(auto, 'ACTIVE', None)
    elif failure == 'time':
        clock.value = control.work_seconds
    else:
        def rejected():
            raise PermissionError('RECOVERY_APPROVAL_CHANGED')
        monkeypatch.setattr(recovery, 'approval', rejected)
    with pytest.raises((PermissionError, RuntimeError)):
        db.queue.claim_next()
    assert db.jobs == before
    assert not any('UPDATE' in sql for sql, _ in db.sql)


def test_extended_window_keeps_native_scope_guard(tmp_path, monkeypatch):
    db = expired_queue(monkeypatch)
    active_window(db, tmp_path, monkeypatch)
    db.jobs[0]['settings_snapshot']['source_input']['entries'].pop()
    assert db.queue.claim_next() is None
    assert db.jobs[0]['status'] == 'failed'
    assert db.jobs[0]['error_message'] == 'BATCH_IMMUTABLE_SCOPE_CHANGED'
