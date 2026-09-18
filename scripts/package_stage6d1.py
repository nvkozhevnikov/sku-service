#!/usr/bin/env python3
"""Build and verify the secret-free Stage 6D.1 corrected archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE6D_RUSSIAN_OPERATOR_ADMIN_REVIEW_CORRECTED"
OUTPUT = SOURCE.parents[2] / f"{ARCHIVE_ROOT}.zip"
EXCLUDED_DIRS = {".git", ".pytest_cache", ".venv", "__pycache__", "backups", "node_modules", "venv"}
EXCLUDED_SUFFIXES = {".bak", ".dump", ".key", ".pyc", ".pyo", ".sql.gz"}
REQUIRED_REPORTS = {
    "STAGE6D1_SUMMARY.txt", "STAGE6D1_QA_REPORT.md", "STAGE6D1_RUNTIME_PIPELINE.md",
    "STAGE6D1_IDENTITY_PARITY_QA.json", "STAGE6D1_REVIEW_SYNC_QA.json",
    "STAGE6D1_MANUAL_REVALIDATION_QA.json", "STAGE6D1_CANDIDATE_TRUTH_QA.json",
    "STAGE6D1_RUSSIAN_EXPLANATION_QA.json", "STAGE6D1_SCHEDULER_UX_QA.md",
    "STAGE6D1_HOSTING_READINESS_QA.md",
}
REQUIRED_SCREENSHOTS = {f"{n:02d}_{name}.png" for n, name in (
    (1, "login"), (2, "dashboard_ru"), (3, "products_ru"), (4, "review_queue"),
    (5, "review_case_comparison"), (6, "supplier_page"), (7, "runs_ru"),
    (8, "scheduler_basic"), (9, "scheduler_advanced"), (10, "users"),
)}

def excluded(relative: Path) -> bool:
    name = relative.name.lower()
    if set(relative.parts) & EXCLUDED_DIRS:
        return True
    if any(part.startswith("pytest-cache-files-") for part in relative.parts):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in {"id_rsa", "id_ed25519"} or "access_token" in name or "session_secret" in name:
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)

def main() -> None:
    reports = {p.name for p in (SOURCE / "reports").glob("STAGE6D1_*") if p.is_file()}
    screenshots = {p.name for p in (SOURCE / "reports" / "screenshots").glob("*.png")}
    missing_reports = sorted(REQUIRED_REPORTS - reports)
    missing_screenshots = sorted(REQUIRED_SCREENSHOTS - screenshots)
    if missing_reports or missing_screenshots:
        raise SystemExit(f"Missing reports={missing_reports}; screenshots={missing_screenshots}")
    files: list[Path] = []
    for root, dirs, names in os.walk(SOURCE, onerror=lambda _error: None):
        root_path = Path(root)
        rel_root = root_path.relative_to(SOURCE)
        dirs[:] = sorted(d for d in dirs if not excluded(rel_root / d))
        files.extend(root_path / n for n in sorted(names) if not excluded((root_path / n).relative_to(SOURCE)))
    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(SOURCE).as_posix()}")
    with zipfile.ZipFile(OUTPUT) as archive:
        names = archive.namelist()
        if not names or any(not n.startswith(ARCHIVE_ROOT + "/") for n in names):
            raise SystemExit("Archive root verification failed")
        if any("/.env" in n and not n.endswith("/.env.example") for n in names):
            raise SystemExit("Forbidden environment file packaged")
    print(f"FILES_PACKAGED = {len(files)}")
    print(f"ARCHIVE = {OUTPUT}")
    print(f"SHA256 = {hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")

if __name__ == "__main__":
    main()
