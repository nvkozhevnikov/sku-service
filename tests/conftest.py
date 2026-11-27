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


LIVE11_APPROVED_MATRIX = ROOT / "tests" / "fixtures" / "live11_approval_matrix_20261022.csv"


@pytest.fixture(autouse=True)
def _live11_approved_matrix(monkeypatch):
    """LIVE11 batch tests run on the approved matrix bytes that MATRIX_SHA pins.

    config/live11_approval_matrix.csv was re-signed on 2026-11-05 for the
    commercial continuation; the frozen LIVE11 batch keeps the 2026-10-22
    approval. Whether production LIVE11 should follow is the owner's call
    (see test_review_fixes_20261126.test_live11_matrix_is_approved).
    """
    from universal_supplier.control_plane import source_batch
    monkeypatch.setattr(source_batch, "MATRIX", LIVE11_APPROVED_MATRIX)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_for_missing_evidence(error)
        raise


# Seal/provenance gates of the LTT/USS checkpoints assert "<NAME>_MISSING" when
# their pinned evidence is absent. Only when that whole local evidence root is
# missing (clean clone, CI) is this a skip; with the evidence present the gate
# runs and fails as written.
LTT_USS_EVIDENCE = ROOT / "reports" / "LTT_USS_INTEGRATION_2026-10-13"
_MISSING_GATE = re.compile(r"[A-Z0-9_]+_MISSING")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_for_missing_evidence(error)
        raise
    except AssertionError as error:
        text = str(error)
        gate = _MISSING_GATE.fullmatch(text) or re.search(r"Actual message: '([A-Z0-9_]+_MISSING)'", text)
        if gate and not LTT_USS_EVIDENCE.exists():
            raise pytest.skip.Exception(f"local evidence not in this checkout (git-ignored/uncommitted): "
                                        f"{_display(LTT_USS_EVIDENCE)} ({error})", _use_item_location=True) from None
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
