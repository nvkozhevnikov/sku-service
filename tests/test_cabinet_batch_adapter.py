"""Adapter boundary only: no PostgreSQL or HTTP connections."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cabinet_batch_adapter',
    Path(__file__).resolve().parents[1] / 'scripts/cabinet_batch_read.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_unapproved_commands_never_connect(monkeypatch):
    monkeypatch.delenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM', raising=False)
    monkeypatch.setattr(adapter.PostgresJobQueue, '_connect', lambda self: pytest.fail('must not connect'))
    for action in ('enqueue', 'stop', 'resume', 'confirm-beka'):
        with pytest.raises(PermissionError, match='WRITE_SESSION_NOT_APPROVED'):
            adapter.command(action, {})


def test_wrong_target_admission_is_not_accepted(monkeypatch):
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM', 'universal_supplier_server')
    with pytest.raises(PermissionError):
        adapter.command('enqueue', {})


def test_reader_never_uses_serving_db_or_mutates(monkeypatch):
    captured = []
    def unavailable(queue):
        captured.append(queue.config)
        raise OSError('offline, credentials must not escape')
    monkeypatch.setattr(adapter.PostgresJobQueue, '_connect', unavailable)
    result = adapter.read('preview')
    assert len(result['matrix']) == 11 and result['launch_allowed'] is False
    assert result['reason'] == 'LIVE11_TARGET_NOT_CONNECTED_OR_IDENTITY_UNVERIFIED'
    assert captured[0].port == 55461 and captured[0].dbname == 'rc_verify_vi_20261019'
    with pytest.raises(ValueError):
        adapter.read('enqueue')
    with pytest.raises(ValueError):
        adapter.read('status', '../bad')


def test_superuser_still_blocks_all_ui_commands(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM', 'rc_verify_vi_20261019')
    cursor = SimpleNamespace(execute=lambda *args: None, fetchone=lambda: ('rc_owner', True))
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    monkeypatch.setattr(adapter.PostgresJobQueue, '_connect', lambda self: nullcontext(connection))
    monkeypatch.setattr(adapter.source_canary, 'validate_connection', lambda conn: None)
    monkeypatch.setattr(adapter.PostgresJobQueue, 'enqueue_source_batch',
                        lambda self, **kwargs: pytest.fail('superuser must never enqueue'))
    for action in ('enqueue', 'stop', 'resume', 'confirm-beka'):
        with pytest.raises(PermissionError, match='ROLE_PREREQUISITE_UNRESOLVED'):
            adapter.command(action, {})
