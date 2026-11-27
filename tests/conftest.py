"""Shared test helpers.

Some regression tests replay local evidence that is deliberately not committed
(``reports/`` and ``RC_LOCAL`` are git-ignored; ``SUMMARY.txt`` and
``docker-compose.qa.yml`` live only in operator checkouts).
On a clean clone those tests SKIP with an explicit reason instead of failing.
The assertions themselves are unchanged and still run wherever the evidence exists.

Web-UI tests that only need *some* packaged identity snapshot use
``control_plane_snapshot_dir()``: the real ``reports/`` evidence when present,
otherwise the committed synthetic fixture ``tests/fixtures/control_plane_snapshot``.
Production ``CONTROL_PLANE_DEMO_SNAPSHOT`` still reads ``reports/`` only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from _support import (  # noqa: F401  re-exported for older imports
    CONTROL_PLANE_SNAPSHOT_FILES, CONTROL_PLANE_SNAPSHOT_FIXTURE, LOCAL_EVIDENCE_DIRS, LOCAL_EVIDENCE_FILES,
    ROOT, _display, _skip_for_missing_evidence, control_plane_snapshot_dir, is_local_evidence, require_evidence,
)


_MISSING_FILE = re.compile(r"FileNotFoundError: \[Errno 2\] No such file or directory: '([^']+)'")


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """A test module that reads local evidence at import time skips like a test does."""
    report = yield
    if report.failed and isinstance(collector, pytest.Module):
        text = str(report.longrepr)
        missing = _MISSING_FILE.findall(text)
        if missing and text.rstrip().splitlines()[-1].startswith("E   FileNotFoundError") \
                and is_local_evidence(missing[-1]) and not Path(missing[-1]).exists():
            report.outcome = "skipped"
            report.longrepr = (str(collector.path), 0, "Skipped: local evidence not in this checkout "
                               f"(git-ignored/uncommitted): {_display(Path(missing[-1]))}")
    return report


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_for_missing_evidence(error)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_for_missing_evidence(error)
        raise


@pytest.fixture(autouse=True)
def _plain_http_test_client(monkeypatch):
    """TestClient talks to http://testserver; a Secure cookie would never be sent back.

    Production keeps the Secure default; tests that check it delete this variable.
    """
    monkeypatch.setenv("CONTROL_PLANE_INSECURE_HTTP", "YES")


_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})


@pytest.fixture(autouse=True)
def _no_external_dns(monkeypatch):
    """Supplier hosts are never resolved from tests; loopback (scratch DB, TestClient) is allowed."""
    import socket
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in _LOOPBACK_NAMES:
            raise AssertionError(f"test tried to resolve external host {name!r}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
