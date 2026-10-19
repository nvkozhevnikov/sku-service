"""Shared test helpers.

Some regression tests replay local evidence that is deliberately not committed
(``reports/`` and ``RC_LOCAL`` are git-ignored; historical stage baselines,
``SUMMARY.txt`` and ``docker-compose.qa.yml`` live only in operator checkouts).
On a clean clone those tests SKIP with an explicit reason instead of failing.
The assertions themselves are unchanged and still run wherever the evidence exists.

Web-UI tests that only need *some* packaged identity snapshot use
``control_plane_snapshot_dir()``: the real ``reports/`` evidence when present,
otherwise the committed synthetic fixture ``tests/fixtures/control_plane_snapshot``.
Production ``CONTROL_PLANE_DEMO_SNAPSHOT`` still reads ``reports/`` only.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LOCAL_EVIDENCE_DIRS = frozenset({"reports", "RC_LOCAL"})
LOCAL_EVIDENCE_FILES = frozenset({
    "SUMMARY.txt",
    "docker-compose.qa.yml",
    "migration_baseline_sha256.json",
    "stage3a1_migration_baseline_sha256.json",
    "stage3a2_migration_baseline_sha256.json",
    "stage3d_migration_baseline_sha256.json",
    "stage6c1_migration_baseline_sha256.json",
})


CONTROL_PLANE_SNAPSHOT_FILES = ("OPTIMUM_IDENTITY_AUDIT.csv", "PARTNER_ST_IDENTITY_REGRESSION.csv")
CONTROL_PLANE_SNAPSHOT_FIXTURE = ROOT / "tests" / "fixtures" / "control_plane_snapshot"


def control_plane_snapshot_dir() -> Path:
    """Directory for SnapshotControlPlaneStore in UI tests.

    ``CONTROL_PLANE_TEST_SNAPSHOT=fixture`` forces the synthetic fixture,
    ``=reports`` forces the real evidence (skipping when absent); by default the
    real ``reports/`` is used when both CSVs exist, else the synthetic fixture.
    """
    reports = ROOT / "reports"
    mode = os.environ.get("CONTROL_PLANE_TEST_SNAPSHOT", "").strip().lower()
    if mode == "fixture":
        return CONTROL_PLANE_SNAPSHOT_FIXTURE
    if mode == "reports" or all((reports / name).exists() for name in CONTROL_PLANE_SNAPSHOT_FILES):
        return reports
    return CONTROL_PLANE_SNAPSHOT_FIXTURE


def is_local_evidence(path: str | Path) -> bool:
    """True for paths inside the project that belong to uncommitted local evidence."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        relative = candidate.resolve().relative_to(ROOT)
    except ValueError:
        return False
    parts = relative.parts
    if not parts:
        return False
    return bool(LOCAL_EVIDENCE_DIRS.intersection(parts)) or relative.as_posix() in LOCAL_EVIDENCE_FILES


def require_evidence(*paths: str | Path) -> None:
    """Skip the current test when any of the given local evidence paths is absent."""
    for path in paths:
        candidate = Path(path) if Path(path).is_absolute() else ROOT / path
        if not candidate.exists():
            pytest.skip(f"local evidence not in this checkout (git-ignored/uncommitted): {_display(candidate)}")


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _skip_for_missing_evidence(error: FileNotFoundError) -> None:
    filename = error.filename
    if filename and is_local_evidence(filename) and not Path(filename).exists():
        raise pytest.skip.Exception(
            f"local evidence not in this checkout (git-ignored/uncommitted): {_display(Path(filename))}",
            _use_item_location=True,
        ) from None


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
