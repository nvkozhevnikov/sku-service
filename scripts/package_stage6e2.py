#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "UNIVERSAL_SUPPLIER_STAGE6E_OPERATOR_DATA_TOOLS_PROPERTIES_FINAL"


def output_zip() -> Path:
    try:
        return ROOT.parents[1] / f"{PACKAGE_NAME}.zip"
    except IndexError:
        return ROOT.parent / f"{PACKAGE_NAME}.zip"
REQUIRED_REPORTS = {
    "STAGE6E2_SUMMARY.txt", "STAGE6E2_QA_REPORT.md",
}


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
        or name.startswith("_debug_") or name.startswith("_qa_stage6e1_") or name.startswith("_qa_stage6e2_")
        or name.endswith(".pem") or "docker-volume" in parts
    )


def main() -> None:
    reports = {p.name for p in (ROOT / "reports").iterdir() if p.is_file() and p.name.startswith("STAGE6E2_")}
    missing = REQUIRED_REPORTS - reports
    if missing:
        raise SystemExit(f"missing Stage 6E.2 reports: {sorted(missing)}")
    files = [p for p in ROOT.rglob("*") if p.is_file() and not forbidden(p.relative_to(ROOT))]
    names_on_disk = {p.name for p in files}
    if ".env.example" not in names_on_disk:
        raise SystemExit(".env.example must be packaged")
    if ".env" in names_on_disk:
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
        names = archive.namelist()
    if bad:
        raise SystemExit(f"CRC failure: {bad}")
    if len({name.split("/")[0] for name in names}) != 1:
        raise SystemExit("archive has more than one root")
    packaged = {Path(name).name for name in names}
    if ".env.example" not in packaged:
        raise SystemExit("packaged artifact is missing .env.example")
    if ".env" in packaged:
        raise SystemExit("packaged artifact contains .env")
    if any(forbidden(Path(name)) for name in names):
        raise SystemExit("forbidden entry detected")
    print(f"OUTPUT_ZIP = {OUTPUT}")
    print(f"ZIP_ENTRIES = {len(names)}")
    print(f"SHA256 = {digest}")
    print("ENV_EXAMPLE = present")
    print("DOTENV = absent")
    print("CRC = PASS")
    print("FORBIDDEN_ENTRIES = 0")


if __name__ == "__main__":
    main()
