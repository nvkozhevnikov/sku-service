#!/usr/bin/env python3
"""Create and validate the self-contained Stage 6 checkpoint ZIP."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "UNIVERSAL_SUPPLIER_STAGE6_STERBRUST_IMPORT_CONTRACT_DRY_RUN.zip"
MANIFEST = ROOT / "reports" / "STAGE6_PACKAGE_MANIFEST.json"
ROOT_NAME = ROOT.name

REQUIRED = {
    "reports/STAGE6_SUMMARY.txt",
    "reports/STAGE6_QA_REPORT.md",
    "reports/STAGE6_IMPORT_CONTRACT.md",
    "reports/STAGE6_IMPORT_FORMAT_MAPPING.md",
    "reports/STAGE6_WRITE_METHOD_CONTRACT.md",
    "reports/STAGE6_WRITE_FIELD_ALLOWLIST.json",
    "reports/STAGE6_AVAILABILITY_CONTRACT.md",
    "reports/STAGE6_PRICE_CONTRACT.md",
    "reports/STAGE6_QUANTITY_CONTRACT.md",
    "reports/STAGE6_LIVE_BEFORE_SNAPSHOT.csv",
    "reports/STAGE6_LIVE_AFTER_SNAPSHOT.csv",
    "reports/STAGE6_IMPORT_PLAN.csv",
    "reports/STAGE6_IMPORT_PLAN_SUMMARY.json",
    "reports/STAGE6_IDENTITY_TARGET_AUDIT.csv",
    "reports/STAGE6_FALSE_MATCH_IMPORT_GUARD.csv",
    "reports/STAGE6_PP800F_DRY_RUN.md",
    "reports/STAGE6_ROLLBACK_PLAN.csv",
    "reports/STAGE6_CANARY_PLAN.csv",
    "reports/STAGE6_BLAST_RADIUS_QA.json",
    "reports/STAGE6_ZERO_WRITE_QA.json",
    "reports/STAGE6_IMPORT_PAYLOAD_VALIDATION.json",
    "reports/STAGE6_CHECKPOINT_PROVENANCE.json",
    "exports/sterbrust_selected_offers.xml",
    "universal_supplier/sterbrust_import_contract.py",
    "tests/test_stage6.py",
}


def excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    lower_parts = {part.casefold() for part in relative.parts}
    if lower_parts & {"__pycache__", ".pytest_cache", ".venv", "venv", "cache", "backups"}:
        return True
    name = path.name.casefold()
    if name in {".env", "docker-compose.override.yml"}:
        return True
    if path.suffix.casefold() in {".pyc", ".pyo", ".dump", ".bak"}:
        return True
    return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_files() -> list[Path]:
    return sorted(
        (path for path in ROOT.rglob("*") if path.is_file() and not excluded(path)),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def main() -> None:
    files_before_manifest = [path for path in package_files() if path != MANIFEST]
    relative_before = {path.relative_to(ROOT).as_posix() for path in files_before_manifest}
    missing = sorted(REQUIRED - relative_before - {"reports/STAGE6_PACKAGE_MANIFEST.json"})
    if missing:
        raise SystemExit("Missing required Stage 6 artifacts: " + ", ".join(missing))

    manifest = {
        "package_root": ROOT_NAME,
        "package_filename": OUTPUT.name,
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "included_file_count_excluding_manifest": len(files_before_manifest),
        "excluded_content": [
            ".env", "REST tokens", "DB passwords", "API secrets", "backup dumps",
            "Docker volume", "venv", "cache", "*.pyc",
        ],
        "required_artifacts_present": True,
        "canonical_stage5_xml_sha256": sha256(ROOT / "exports" / "sterbrust_selected_offers.xml"),
        "migrations": sorted(path.name for path in (ROOT / "migrations").glob("*.sql")),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    files = package_files()
    if OUTPUT.exists():
        OUTPUT.unlink()
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            arcname = f"{ROOT_NAME}/{path.relative_to(ROOT).as_posix()}"
            archive.write(path, arcname)

    with zipfile.ZipFile(OUTPUT, "r") as archive:
        names = archive.namelist()
        bad = [name for name in names if any(part.casefold() in {"__pycache__", ".pytest_cache", ".venv", "venv", "backups"} for part in Path(name).parts)]
        bad.extend(name for name in names if Path(name).name.casefold() == ".env" or Path(name).suffix.casefold() in {".pyc", ".pyo", ".dump", ".bak"})
        if bad:
            raise SystemExit("Excluded content leaked into package: " + ", ".join(sorted(set(bad))))
        expected_prefix = ROOT_NAME + "/"
        if any(not name.startswith(expected_prefix) for name in names):
            raise SystemExit("ZIP contains an unexpected root")
        packaged_relative = {name[len(expected_prefix):] for name in names}
        missing = sorted(REQUIRED - packaged_relative)
        if missing:
            raise SystemExit("ZIP missing required artifacts: " + ", ".join(missing))
        bad_member = archive.testzip()
        if bad_member:
            raise SystemExit(f"CRC verification failed for {bad_member}")

    result = {
        "result": "PASS",
        "path": str(OUTPUT),
        "size_bytes": OUTPUT.stat().st_size,
        "sha256": sha256(OUTPUT),
        "file_count": len(files),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
