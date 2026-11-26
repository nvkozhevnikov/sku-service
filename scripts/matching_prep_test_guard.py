"""Session guard for code-only matching preparation tests. Fakes may override it."""
from unittest.mock import patch

_patches = []


def _forbidden(*args, **kwargs):
    raise AssertionError('Real PostgreSQL/network calls forbidden in matching-prep tests')


def pytest_sessionstart(session):
    for name in ('psycopg.connect', 'socket.socket.connect', 'socket.create_connection'):
        guard = patch(name, _forbidden)
        guard.start()
        _patches.append(guard)


def pytest_sessionfinish(session, exitstatus):
    for guard in reversed(_patches):
        guard.stop()
    _patches.clear()
