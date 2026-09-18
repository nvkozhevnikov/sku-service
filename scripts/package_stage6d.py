#!/usr/bin/env python3
"""Build the secret-free, self-contained Stage 6D delivery archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile


SOURCE = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE6D_RUSSIAN_OPERATOR_ADMIN_REVIEW"
OUTPUT = SOURCE.parents[2] / f"{ARCHIVE_ROOT}.zip"

EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backups",
    "node_modules",
    "venv",
}
EXCLUDED_SUFFIXES = {".bak", ".dump", ".key", ".pyc", ".pyo", ".sql.gz"}
REQUIRED_REPORTS = {
    "STAGE6D_SUMMARY.txt",
    "STAGE6D_QA_REPORT.md",
    "STAGE6D_RUSSIAN_UI.md",
    "STAGE6D_REVIEW_WORKFLOW.md",
    "STAGE6D_REVIEW_SCHEMA.md",
    "STAGE6D_AUTH_ROLES.md",
    "STAGE6D_AUDIT_LOG.md",
    "STAGE6D_MANUAL_CRAWL.md",
    "STAGE6D_MIGRATION013_QA.md",
    "STAGE6D_DOCKER_QA.md",
    "STAGE6D_SECURITY_QA.md",
    "STAGE6D_UX_QA.md",
}
REQUIRED_SCREENSHOTS = {f"{number:02d}_{name}.png" for number, name in (
    (1, "login"),
    (2, "dashboard_ru"),
    (3, "products_ru"),
    (4, "review_queue"),
    (5, "review_case_comparison"),
    (6, "supplier_page"),
    (7, "runs_ru"),
    (8, "scheduler_basic"),
    (9, "scheduler_advanced"),
    (10, "users"),
)}


def excluded(relative: Path) -> bool:
    parts = set(relative.parts)
    name = relative.name.lower()
    if parts & EXCLUDED_DIRS:
        return True
    if any(part.startswith("pytest-cache-files-") for part in relative.parts):
        return True
    if name == ".env" or name.startswith(".env.") and name != ".env.example":
        return True
    if name in {"id_rsa", "id_ed25519"} or "session_secret" in name or "access_token" in name:
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def main() -> None:
    report_names = {path.name for path in (SOURCE / "reports").glob("STAGE6D*") if path.is_file()}
    screenshot_names = {path.name for path in (SOURCE / "reports" / "screenshots").glob("*.png")}
    missing_reports = sorted(REQUIRED_REPORTS - report_names)
    missing_screenshots = sorted(REQUIRED_SCREENSHOTS - screenshot_names)
    if missing_reports or missing_screenshots:
        raise SystemExit(f"Missing reports={missing_reports}; screenshots={missing_screenshots}")

    files: list[Path] = []
    for root, dirs, names in os.walk(SOURCE, onerror=lambda _error: None):
        root_path = Path(root)
        relative_root = root_path.relative_to(SOURCE)
        dirs[:] = sorted(name for name in dirs if not excluded(relative_root / name))
        for name in sorted(names):
            path = root_path / name
            relative = path.relative_to(SOURCE)
            if not excluded(relative):
                files.append(path)

    OUTPUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(SOURCE).as_posix()
            archive.write(path, f"{ARCHIVE_ROOT}/{relative}")

    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"FILES_PACKAGED = {len(files)}")
    print(f"ARCHIVE = {OUTPUT}")
    print(f"SHA256 = {digest}")


if __name__ == "__main__":
    main()
