"""Actual PostgreSQL upsert regression in a private temporary cluster, no HTTP.

Set COMPETITOR_TEST_PG_BIN to the installed PostgreSQL bin directory. No business
DSN/config is read, and production Store identity validation remains unchanged.
"""
import os
import shutil
import socket
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from competitor_intel.collection_adapter import collect_source
from competitor_intel.execution import settings
from competitor_intel.model import Page
from competitor_intel.store import Store
from competitor_intel.transport import Capture


@pytest.fixture(scope='module')
def isolated_store(tmp_path_factory):
    binary = os.environ.get('COMPETITOR_TEST_PG_BIN')
    pg_ctl = Path(binary) / 'pg_ctl' if binary else Path(shutil.which('pg_ctl') or '')
    suffix = '.exe' if os.name == 'nt' else ''
    pg_ctl = pg_ctl.with_suffix(suffix)
    if not pg_ctl.is_file():
        pytest.fail('Set COMPETITOR_TEST_PG_BIN; an isolated PostgreSQL runtime is required')
    root = tmp_path_factory.mktemp('surface_pg')
    data = root / 'data'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def command(args):
        # Windows server descendants may retain pipe handles even with pg_ctl
        # -l. File output avoids waiting for a running server to close a pipe.
        with (root / 'commands.log').open('ab') as output:
            return subprocess.run(args, check=True, stdout=output, stderr=output,
                                  timeout=40, creationflags=flags)

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # New cluster and new sequences only; never contact the business port55454.
    assert port != 55454
    command([str(pg_ctl.parent / ('initdb' + suffix)), '-D', str(data),
             '-U', 'competitor_dev', '--auth=trust', '--encoding=UTF8', '--no-locale'])
    started = False
    store = None
    try:
        command([str(pg_ctl), '-D', str(data), '-l', str(root / 'postgres.log'),
                 '-o', f'-h 127.0.0.1 -p {port}', '-w', 'start'])
        started = True
        params = dict(host='127.0.0.1', port=port, user='competitor_dev')
        with psycopg.connect(**params, dbname='postgres', autocommit=True) as conn:
            # PostgreSQL on Windows can return a native-codepage path setting
            # despite UTF8 database encoding; preserve its bytes for identity.
            raw = conn.execute("SELECT current_setting('data_directory')::bytea").fetchone()[0]
            try:
                actual = raw.decode('utf-8')
            except UnicodeDecodeError:
                if os.name != 'nt':
                    raise
                actual = raw.decode('mbcs')
            assert Path(actual).resolve() == data.resolve()
            conn.execute('CREATE DATABASE sterbrust_competitor_intel')
        # Explicit test adapter: reuse actual Store.save/query methods against
        # the isolated cluster, without weakening production constructor guards.
        store = Store.__new__(Store)
        store.conn = psycopg.connect(**params, dbname='sterbrust_competitor_intel', row_factory=dict_row)
        store.migrate()
        with store.conn.transaction(), store.conn.cursor() as cursor:
            cursor.execute(Path('competitor_intel/kuvalda_surfaces.sql').read_text(encoding='utf-8'))
            for code in settings()['selected_sources']:
                cursor.execute('INSERT INTO competitor_sources(code,name,base_url) VALUES(%s,%s,%s) ON CONFLICT(code) DO NOTHING',
                               (code, code, f'https://{code}.example/'))
        yield store
    finally:
        if store is not None:
            store.close()
        if started:
            command([str(pg_ctl), '-D', str(data), '-m', 'fast', '-w', 'stop'])


@pytest.mark.parametrize('accepted', [True, False], ids=['accepted-page', 'fetched-index'])
def test_surface_refresh_preserves_metadata_and_observation_history(isolated_store, tmp_path, monkeypatch, accepted):
    store = isolated_store
    url = 'https://metalmaster.ru/'
    # Each parameter has independent synthetic state inside the disposable DB.
    with store.conn.transaction(), store.conn.cursor() as cursor:
        cursor.execute('TRUNCATE competitor_source_surfaces,competitor_runs,competitor_pages CASCADE')
    assert len(store.query('SELECT code FROM competitor_sources')) == 11
    # Scheduler state is intentionally not asserted here: this regression must hold for both disabled and enabled repository schedules (repository-state coverage: tests/test_onboarding_gap_closure.py).
    observed = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def forbidden(*args, **kwargs):
        pytest.fail('External HTTP is forbidden in provenance regression')

    monkeypatch.setattr('requests.sessions.Session.request', forbidden)

    class SavedTransport:
        def __init__(self, config, directory):
            self.session = SimpleNamespace(get=forbidden, close=lambda: None)
            self.events = []

        def prepare(self):
            pass

        def permitted(self, target):
            return target == url

        def robots_disallowed(self, target):
            return False

        def get(self, target):
            assert target == url
            self.events.append({'url': target})
            return Capture(url, url, '<h1>Saved fixture</h1>', str(tmp_path / str(observed.day)),
                           str(observed.day), observed, 'FIXTURE')

    def parsed(*args):
        return Page('metalmaster', url, url, 'Saved fixture', 'HOMEPAGE_BLOCK',
                    'Saved fixture', 'Saved fixture', inclusion_reason='CURRENT_PROMO' if accepted else None)

    monkeypatch.setattr('competitor_intel.collection_adapter.SourceTransport', SavedTransport)
    monkeypatch.setattr('competitor_intel.parser.parse', parsed)
    monkeypatch.setattr('competitor_intel.parser.discover', lambda *args: [])
    repo = SimpleNamespace(store=store)
    since = date(2026, 9, 1)
    run_a = store.run_start(since)
    first = collect_source(repo, 'metalmaster', run_a, since, tmp_path, 1, time.monotonic() + 10)
    assert not first['errors']
    a = store.query('SELECT * FROM competitor_source_surfaces')[0]
    assert a['details'] == {'run_id': run_a, 'scope_complete': False}
    historical = store.query('SELECT * FROM competitor_observations ORDER BY id')
    persistent = {'source_config': {'region': 'fixture'},
                  'automated_access_evidence': {'fetch_status': 'HTTP_BLOCKED'},
                  'diagnostics': [{'reason': 'previous failure'}]}
    with store.conn.transaction(), store.conn.cursor() as cursor:
        cursor.execute('UPDATE competitor_source_surfaces SET details=details || %s || %s',
                       (Jsonb(persistent), Jsonb({'scope_complete': True})))
    observed = datetime(2026, 10, 2, tzinfo=timezone.utc)
    run_b = store.run_start(since)
    second = collect_source(repo, 'metalmaster', run_b, since, tmp_path, 1, time.monotonic() + 10)
    assert not second['errors']
    rows = store.query('SELECT * FROM competitor_source_surfaces')
    assert len(rows) == 1 and rows[0]['id'] == a['id']
    b = rows[0]
    assert b['observed_at'] == observed and b['raw_capture_ref'] == str(tmp_path / '2')
    assert b['details']['run_id'] == run_b
    assert b['details']['scope_complete'] is False
    assert {key: b['details'][key] for key in persistent} == persistent
    observations = store.query('SELECT * FROM competitor_observations ORDER BY id')
    assert observations[:len(historical)] == historical
    assert [row['run_id'] for row in observations] == ([run_a, run_b] if accepted else [])
    if accepted:
        assert observations[0]['page_version_id'] == observations[1]['page_version_id']
        assert all(row['qa_status'] == 'ACCEPTED' for row in store.query('SELECT qa_status FROM competitor_page_versions'))
    assert [row['id'] for row in store.query('SELECT id FROM competitor_runs ORDER BY id')] == [run_a, run_b]
