#!/usr/bin/env python3
"""Build and verify the secret-free Stage 6D.2 final archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE6D_RUSSIAN_OPERATOR_ADMIN_REVIEW_FINAL"
OUTPUT = SOURCE.parents[2] / f"{ARCHIVE_ROOT}.zip"
EXCLUDED_DIRS = {".git", ".pytest_cache", ".venv", "__pycache__", "backups", "node_modules", "venv"}
EXCLUDED_SUFFIXES = {".bak", ".dump", ".key", ".pyc", ".pyo", ".sql.gz"}
REQUIRED_REPORTS = {
    "STAGE6D2_SUMMARY.txt", "STAGE6D2_QA_REPORT.md", "STAGE6D2_RUN_STATUS_QA.json",
    "STAGE6D2_RUSSIAN_UI_QA.json", "STAGE6D2_RUNTIME_QA.json",
    "STAGE6D2_IDENTITY_PARITY_QA.json",
}
REQUIRED_SCREENSHOTS = {
    "STAGE6D2_01_dashboard.png", "STAGE6D2_02_products.png",
    "STAGE6D2_03_product_detail.png", "STAGE6D2_04_review_queue.png",
    "STAGE6D2_05_review_case.png", "STAGE6D2_06_suppliers.png",
    "STAGE6D2_07_runs.png", "STAGE6D2_08_scheduler.png",
    "STAGE6D2_09_settings.png",
}


def excluded(relative: Path) -> bool:
    name = relative.name.lower()
    if set(relative.parts) & EXCLUDED_DIRS:
        return True
    if any(part.startswith("pytest-cache-files-") for part in relative.parts):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in {"id_rsa", "id_ed25519"}:
        return True
    if any(marker in name for marker in ("access_token", "session_secret", "credentials")):
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def main() -> None:
    reports = {p.name for p in (SOURCE / "reports").glob("STAGE6D2_*") if p.is_file()}
    screenshots = {p.name for p in (SOURCE / "reports" / "screenshots").glob("STAGE6D2_*.png")}
    missing_reports = sorted(REQUIRED_REPORTS - reports)
    missing_screenshots = sorted(REQUIRED_SCREENSHOTS - screenshots)
    if missing_reports or missing_screenshots:
        raise SystemExit(f"Missing reports={missing_reports}; screenshots={missing_screenshots}")
    if len(list((SOURCE / "migrations").glob("[0-9][0-9][0-9]_*.sql"))) != 13:
        raise SystemExit("Expected exactly immutable migrations 001-013")
    files: list[Path] = []
    for root, dirs, names in os.walk(SOURCE, onerror=lambda _error: None):
        root_path = Path(root)
        rel_root = root_path.relative_to(SOURCE)
        dirs[:] = sorted(d for d in dirs if not excluded(rel_root / d))
        files.extend(root_path / name for name in sorted(names)
                     if not excluded((root_path / name).relative_to(SOURCE)))
    forbidden_value = b"Stage" + b"6d2-"
    for path in files:
        if path.stat().st_size < 5_000_000 and forbidden_value in path.read_bytes():
            raise SystemExit(f"Synthetic QA credential marker found in {path.relative_to(SOURCE)}")
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(SOURCE).as_posix()}")
    with zipfile.ZipFile(OUTPUT) as archive:
        names = archive.namelist()
        if not names or any(not name.startswith(ARCHIVE_ROOT + "/") for name in names):
            raise SystemExit("Archive root verification failed")
        if any("/.env" in name and not name.endswith("/.env.example") for name in names):
            raise SystemExit("Forbidden environment file packaged")
        if any("/__pycache__/" in name or name.endswith((".pyc", ".pyo", ".dump")) for name in names):
            raise SystemExit("Forbidden runtime artifact packaged")
    print(f"FILES_PACKAGED = {len(files)}")
    print(f"ARCHIVE = {OUTPUT}")
    print(f"SHA256 = {hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
