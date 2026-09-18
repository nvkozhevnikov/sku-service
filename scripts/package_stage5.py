#!/usr/bin/env python3
"""Create the sanitized, self-contained Stage 5 checkpoint ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE5_YML_FEED_GENERATOR"
DEFAULT_OUTPUT = ROOT.parents[1] / f"{ARCHIVE_ROOT}.zip"
EXCLUDED_NAMES = {".env", ".git", ".pytest_cache", "__pycache__", "venv", ".venv", "backups"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".dump", ".backup"}
REQUIRED_FILES = {
    "config/yml_feed.json",
    "universal_supplier/yml_feed.py",
    "scripts/generate_yml_feed.py",
    "scripts/validate_yml_feed.py",
    "scripts/run_stage5_verification.py",
    "reports/STAGE5_SUMMARY.txt",
    "reports/STAGE5_QA_REPORT.md",
    "reports/STAGE5_XML_FIELD_MAPPING.md",
    "reports/STAGE5_AVAILABILITY_MAPPING.md",
    "reports/STAGE5_FEED_TRACE.csv",
    "reports/STAGE5_NOT_MANAGED_PRODUCTS.csv",
    "reports/STAGE5_FEED_VALIDATION.json",
    "reports/STAGE5_FEED_MANIFEST.json",
    "reports/STAGE5_IDEMPOTENCY_QA.json",
    "reports/STAGE5_ATOMIC_REPLACE_QA.json",
    "reports/STAGE5_MULTI_SUPPLIER_TRANSACTION_QA.csv",
    "reports/STAGE5_COMMERCIAL_CHANGE_QA.csv",
    "reports/STAGE5_FALSE_MATCH_FEED_GUARD.csv",
    "exports/sterbrust_selected_offers.xml",
}


def included_files() -> list[Path]:
    result = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file():
            continue
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if path.name.lower().endswith(".sql.gz"):
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    missing = sorted(item for item in REQUIRED_FILES if not (ROOT / item).is_file())
    if missing:
        raise SystemExit("Missing required Stage 5 files: " + ", ".join(missing))
    if list((ROOT / "migrations").glob("012_*.sql")):
        raise SystemExit("Unexpected migration 012")
    files = included_files()
    unsafe = [
        path.relative_to(ROOT).as_posix() for path in files
        if path.name == ".env" or path.suffix.lower() in EXCLUDED_SUFFIXES
        or any(part in {"venv", ".venv", "__pycache__", ".pytest_cache", "backups"}
               for part in path.relative_to(ROOT).parts)
    ]
    if unsafe:
        raise SystemExit("Unsafe package members: " + ", ".join(unsafe))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{args.output.name}.", suffix=".tmp", dir=args.output.parent
        )
        os.close(descriptor)
        temporary = Path(name)
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                relative = path.relative_to(ROOT).as_posix()
                archive.write(path, f"{ARCHIVE_ROOT}/{relative}")
        with zipfile.ZipFile(temporary) as archive:
            bad_member = archive.testzip()
            members = archive.namelist()
        if bad_member:
            raise SystemExit(f"ZIP integrity failure: {bad_member}")
        os.replace(temporary, args.output)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({
        "result": "PASS",
        "file": str(args.output.resolve()),
        "sha256": digest,
        "file_count": len(files),
        "archive_member_count": len(members),
        "archive_root": ARCHIVE_ROOT,
        "migration_count": len(list((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))),
        "migration_012": "NOT_REQUIRED",
        "env_files": sum(Path(item).name == ".env" for item in members),
        "cache_or_venv_members": sum(
            any(part in {"venv", ".venv", "__pycache__", ".pytest_cache"}
                for part in Path(item).parts) for item in members
        ),
        "dump_members": sum(
            Path(item).suffix.lower() in {".dump", ".backup"}
            or item.lower().endswith(".sql.gz") for item in members
        ),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
