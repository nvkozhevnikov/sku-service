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


@pytest.mark.parametrize('role', [('rc_owner', True), ('rc_owner', False), ('unknown', False), ('rc_runtime', True)])
def test_superuser_still_blocks_all_ui_commands(monkeypatch, role):
    from contextlib import nullcontext
    from types import SimpleNamespace
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM', 'rc_verify_vi_20261019')
    cursor = SimpleNamespace(execute=lambda *args: None, fetchone=lambda: role)
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    monkeypatch.setattr(adapter.PostgresJobQueue, '_connect', lambda self: nullcontext(connection))
    monkeypatch.setattr(adapter.source_canary, 'validate_connection', lambda conn: None)
    monkeypatch.setattr(adapter.PostgresJobQueue, 'enqueue_source_batch',
                        lambda self, **kwargs: pytest.fail('superuser must never enqueue'))
    for action in ('enqueue', 'stop', 'resume', 'confirm-beka'):
        with pytest.raises(PermissionError, match='ROLE_PREREQUISITE_UNRESOLVED'):
            adapter.command(action, {})


def test_runtime_credentials_are_exact_and_separate_from_legacy(monkeypatch, tmp_path):
    import json
    path = tmp_path / 'credential.json'
    monkeypatch.setattr(adapter, 'CREDENTIAL_FILE', path)
    monkeypatch.setenv('DB_USER', 'rc_owner')
    monkeypatch.setenv('DB_PORT', '55449')
    monkeypatch.setenv('DB_PASSWORD', 'must-not-inherit')
    config = adapter.target_config()
    assert (config.user, config.port, config.password) == ('rc_runtime', 55461, None)
    value = dict(host='127.0.0.1', port=55461, database='rc_verify_vi_20261019', role='rc_runtime', password='offline')
    path.write_text(json.dumps(value), encoding='utf-8')
    assert adapter.target_config().password == 'offline'
    for key, wrong in [('host','other'),('port',55449),('database','other'),('role','rc_owner')]:
        path.write_text(json.dumps({**value,key:wrong}), encoding='utf-8')
        with pytest.raises(PermissionError, match='CREDENTIAL_TARGET_MISMATCH'):
            adapter.target_config()


def test_readiness_reports_every_source_without_activation(monkeypatch):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace
    matrix = adapter.source_batch.matrix_rows()
    rows = [(r['SOURCE'], r['SOURCE'], 'https://' + r['APPROVAL_HOST'], True,
             {'host_policy_approval': {'host': r['APPROVAL_HOST'], 'reference': r['APPROVAL_REFERENCE']}},
             json.loads(r['RATE_POLICY_JSON'])) for r in matrix]
    cursor = SimpleNamespace(execute=lambda *args: None, fetchall=lambda: rows)
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    queue = SimpleNamespace(_connect=lambda: nullcontext(connection))
    monkeypatch.setattr(adapter.source_canary, 'validate_connection', lambda c: None)
    for flag in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):
        monkeypatch.setenv(flag, 'YES')
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES', json.dumps({r['APPROVAL_HOST']:r['APPROVAL_DIGEST_SHA256'] for r in matrix}))
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM','rc_verify_vi_20261019')
    result = adapter.source_readiness(queue)
    assert len(result) == 11 and all(r['setup_ready'] for r in result)
    assert not any(r['launch_allowed'] for r in result)  # Frozen digest still required.
    rows[0] = (*rows[0][:3], False, {}, {})
    monkeypatch.delenv('STERBRUST_ALLOW_SUPPLIER_HTTP')
    result = adapter.source_readiness(queue)
    assert 'BATCH_DISABLED_REQUIRES_EXPLICIT_ENABLE_RESTORE' in result[0]['blockers']
    assert 'BATCH_HOST_POLICY_REQUIRES_EXPLICIT_ACTIVATION' in result[0]['blockers']
    assert all('STERBRUST_ALLOW_SUPPLIER_HTTP_NOT_ENABLED' in r['blockers'] for r in result)
