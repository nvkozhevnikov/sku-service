#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "UNIVERSAL_SUPPLIER_STAGE6E_XML_FULL_PROGRESS_CLOSED"
OUTPUT = ROOT.parents[2] / f"{PACKAGE_NAME}.zip"
REQUIRED_REPORTS = {
    "XML_ADMIN_EXPORT_QA.md", "CRAWL_PROGRESS_ETA_QA.md",
    "XML_REAL_CLONE_QA.json", "XML_MANUAL_MAPPING_QA.json",
    "XML_IDENTITY_CLOSURE_QA.md", "CRAWL_PROGRESS_ETA_CLOSURE_QA.md",
    "STAGE6E_XML_FULL_PROGRESS_CLOSED.md",
}
REQUIRED_SHOTS = {
    "xml_short_export.png", "xml_full_export.png",
    "crawl_running_progress.png", "crawl_running_eta.png",
}


def forbidden(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if name == ".env.example":
        return False
    return (
        bool(parts & {"preview-backup", "backups", "__pycache__", ".pytest_cache", ".venv", "venv", "pgdata"})
        or name == ".env" or name.startswith(".env.")
        or name.endswith((".pyc", ".pyo", ".dump", ".sql.gz", ".partial", ".pem"))
        or name.startswith(("_debug_", "_qa_stage6"))
        or "docker-volume" in parts
    )


def main() -> None:
    reports = {path.name for path in (ROOT / "reports").glob("*") if path.is_file()}
    shots = {path.name for path in (ROOT / "reports" / "screenshots").glob("*.png")}
    missing_reports = REQUIRED_REPORTS - reports
    missing_shots = REQUIRED_SHOTS - shots
    if missing_reports or missing_shots:
        raise SystemExit(f"missing reports={sorted(missing_reports)} screenshots={sorted(missing_shots)}")
    files = [path for path in ROOT.rglob("*") if path.is_file() and not forbidden(path.relative_to(ROOT))]
    disk_names = {path.name for path in files}
    if ".env.example" not in disk_names or ".env" in disk_names:
        raise SystemExit("dotenv packaging contract failed")
    if OUTPUT.exists():
        OUTPUT.unlink()
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(ROOT))
    with ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        names = archive.namelist()
    if bad or len({name.split("/")[0] for name in names}) != 1:
        raise SystemExit(f"archive validation failed: {bad}")
    if any(forbidden(Path(name)) for name in names):
        raise SystemExit("forbidden entry detected")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"OUTPUT_ZIP = {OUTPUT}")
    print(f"ZIP_ENTRIES = {len(names)}")
    print(f"ZIP_BYTES = {OUTPUT.stat().st_size}")
    print(f"SHA256 = {digest}")
    print("CRC = PASS")
    print("FORBIDDEN_ENTRIES = 0")


if __name__ == "__main__":
    main()
