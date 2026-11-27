"""Server compose identity for read-only competitor monitoring."""
import pytest

from competitor_intel import store
from competitor_intel.monitoring import MonitoringRepository


SERVER = {'STERBRUST_COMPETITOR_SERVER': 'YES', 'DB_NAME': 'universal_supplier_server',
          'DB_HOST': 'postgres', 'DB_PORT': '5432', 'DB_SYSTEM_IDENTIFIER': '7712806569708572710'}


def test_server_identity_is_off_by_default(monkeypatch):
    monkeypatch.delenv('STERBRUST_COMPETITOR_SERVER', raising=False)
    assert store.server_identity() is None


def test_server_identity_comes_from_server_environment(monkeypatch):
    for key, value in SERVER.items():
        monkeypatch.setenv(key, value)
    assert store.server_identity() == {'dbname': 'universal_supplier_server', 'host': 'postgres', 'port': '5432',
                                       'user': 'competitor_runtime', 'system': '7712806569708572710'}


@pytest.mark.parametrize('key,value', [('DB_SYSTEM_IDENTIFIER', '0'), ('DB_SYSTEM_IDENTIFIER', ''),
                                       ('DB_NAME', ''), ('DB_PORT', 'x')])
def test_server_identity_fails_closed(monkeypatch, key, value):
    for name, current in SERVER.items():
        monkeypatch.setenv(name, current)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        store.server_identity()


def test_server_identity_rejects_another_role_before_connecting(monkeypatch):
    for key, value in SERVER.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('STERBRUST_UNIFIED_RC', 'YES')
    monkeypatch.setenv('STERBRUST_COMPETITOR_MAIN', 'YES')
    monkeypatch.setattr(store.psycopg, 'connect', lambda *a, **k: pytest.fail('must not connect'))
    with pytest.raises(ValueError, match='identity mismatch'):
        store.Store('host=postgres port=5432 dbname=universal_supplier_server user=universal_supplier_server_app')
    with pytest.raises(ValueError, match='identity mismatch'):
        store.Store('host=postgres port=5432 dbname=other user=competitor_runtime')


def test_repository_takes_exactly_one_source():
    with pytest.raises(ValueError):
        MonitoringRepository()
    with pytest.raises(ValueError):
        MonitoringRepository('a.json', dsn='host=x')
    assert MonitoringRepository(dsn='host=x').dsn == 'host=x'
