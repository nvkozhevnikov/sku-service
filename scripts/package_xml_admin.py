#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "UNIVERSAL_SUPPLIER_STAGE6E_XML_ADMIN_EXPORT_CANDIDATE"


def output_zip() -> Path:
    try:
        return ROOT.parents[1] / f"{PACKAGE_NAME}.zip"
    except IndexError:
        return ROOT.parent / f"{PACKAGE_NAME}.zip"


def forbidden(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if name == ".env.example":
        return False
    backup_tree = bool(parts & {"preview-backup", "backups"})
    temporary_database_export = bool(parts & {"tmp", "temp"}) and name.endswith((".sql", ".dump", ".backup", ".sql.gz"))
    return (
        backup_tree or temporary_database_export
        or
        name == ".env" or name.startswith(".env.") or "__pycache__" in parts or ".pytest_cache" in parts
        or "venv" in parts or ".venv" in parts or name.endswith(".pyc") or name.endswith(".pyo")
        or name.endswith(".dump") or name.endswith(".sql.gz") or "pgdata" in parts
        or name.startswith("test_export_") or name.endswith(".partial")
        or name.startswith("_debug_") or name.startswith("_qa_stage6")
        or name.endswith(".pem") or "docker-volume" in parts
    )


def main() -> None:
    report = ROOT / "reports" / "XML_ADMIN_EXPORT_QA.md"
    shots = {p.name for p in (ROOT / "reports/screenshots").glob("xml_*.png")}
    if not report.is_file():
        raise SystemExit("missing XML_ADMIN_EXPORT_QA.md")
    if "xml_export_form.png" not in shots or "xml_preview.png" not in shots:
        raise SystemExit(f"missing XML screenshots: {sorted(shots)}")
    files = [p for p in ROOT.rglob("*") if p.is_file() and not forbidden(p.relative_to(ROOT))]
    names = {p.name for p in files}
    if ".env.example" not in names:
        raise SystemExit(".env.example must be packaged")
    if ".env" in names:
        raise SystemExit(".env must not be packaged")
    OUTPUT = output_zip()
    if OUTPUT.exists():
        OUTPUT.unlink()
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(ROOT))
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    with ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        entries = archive.namelist()
    if bad:
        raise SystemExit(f"CRC failure: {bad}")
    packaged = {Path(name).name for name in entries}
    if ".env" in packaged or ".env.example" not in packaged:
        raise SystemExit("dotenv packaging contract failed")
    print(f"OUTPUT_ZIP = {OUTPUT}")
    print(f"ZIP_ENTRIES = {len(entries)}")
    print(f"SHA256 = {digest}")
    print("CRC = PASS")


if __name__ == "__main__":
    main()
