#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "UNIVERSAL_SUPPLIER_STAGE6E_CLOSED_FINAL"
REQUIRED_REPORTS = {
    "STAGE6E_FINAL_QA_REPORT.md",
    "XML_ADMIN_EXPORT_QA.md",
    "ADMIN_PREVIEW_CLONE_QA.md",
    "PACKAGE_SAFETY_QA.md",
    "STAGE6E_FINAL_RUNTIME_QA.json",
}


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
    database_export = name.endswith((".dump", ".backup", ".sql.gz"))
    temporary_sql = bool(parts & {"tmp", "temp", "preview-backup", "backups"}) and name.endswith(".sql")
    return (
        bool(parts & {"preview-backup", "backups", "pgdata", "__pycache__", ".pytest_cache", ".venv", "venv"})
        or temporary_sql or database_export
        or name == ".env" or name.startswith(".env.")
        or name.endswith((".pyc", ".pyo", ".pem", ".partial"))
        or name.startswith(("_debug_", "test_export_"))
        or "docker-volume" in parts
    )


def main() -> None:
    output = output_zip()
    reports = ROOT / "reports"
    missing = sorted(name for name in REQUIRED_REPORTS if not (reports / name).is_file())
    if missing:
        raise SystemExit(f"missing final reports: {missing}")
    files = [path for path in ROOT.rglob("*") if path.is_file() and not forbidden(path.relative_to(ROOT))]
    if not any(path.name == ".env.example" for path in files):
        raise SystemExit(".env.example is required")
    if output.exists():
        output.unlink()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(ROOT))
    with ZipFile(output) as archive:
        bad = archive.testzip()
        infos = archive.infolist()
        names = [item.filename for item in infos]
    roots = {name.split("/", 1)[0] for name in names}
    forbidden_entries = [name for name in names if forbidden(Path(name))]
    dump_entries = [name for name in names if name.lower().endswith((".dump", ".backup", ".sql.gz"))]
    backup_entries = [name for name in names if {part.lower() for part in Path(name).parts} & {"preview-backup", "backups"}]
    if bad or roots != {PACKAGE_NAME} or forbidden_entries or dump_entries or backup_entries:
        raise SystemExit(
            f"unsafe archive: bad={bad}, roots={roots}, forbidden={forbidden_entries[:5]}, "
            f"dumps={dump_entries[:5]}, backups={backup_entries[:5]}"
        )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"OUTPUT_ZIP = {output}")
    print(f"ZIP_ENTRIES = {len(names)}")
    print(f"UNCOMPRESSED_BYTES = {sum(item.file_size for item in infos)}")
    print(f"COMPRESSED_BYTES = {output.stat().st_size}")
    print(f"DB_DUMP_ENTRIES = {len(dump_entries)}")
    print(f"PREVIEW_BACKUP_ENTRIES = {len(backup_entries)}")
    print(f"ROOT = {next(iter(roots))}")
    print(f"SHA256 = {digest}")
    print("CRC = PASS")


if __name__ == "__main__":
    main()
