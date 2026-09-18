#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "UNIVERSAL_SUPPLIER_STAGE6E_OPERATOR_DATA_TOOLS_PROPERTIES_CORRECTED"


def output_zip() -> Path:
    try:
        return ROOT.parents[1] / f"{PACKAGE_NAME}.zip"
    except IndexError:
        return ROOT.parent / f"{PACKAGE_NAME}.zip"
REQUIRED_REPORTS = {
    "STAGE6E1_SUMMARY.txt", "STAGE6E1_QA_REPORT.md", "STAGE6E1_RUNTIME_QUALITY_QA.json",
    "STAGE6E1_MISSING_EVIDENCE_QA.json", "STAGE6E1_FIELD_PROVENANCE_QA.json", "STAGE6E1_REPARSE_QA.json",
    "STAGE6E1_PROPERTY_DISCOVERY_QA.json", "STAGE6E1_PROPERTY_UI_QA.md", "STAGE6E1_BUSINESS_RULES_QA.json",
    "STAGE6E1_FIELD_OWNERSHIP_PREVIEW_QA.json", "STAGE6E1_STERBRUST_PREVIEW_QA.json", "STAGE6E1_EXPORT_SCALE_QA.md",
}
REQUIRED_SCREENSHOTS = {
    "stage6e1_data_quality.png", "stage6e1_properties.png", "stage6e1_rules.png",
    "stage6e1_product_detail.png", "stage6e1_reparse.png", "stage6e1_sterbrust_preview.png",
    "stage6e1_products_export.png",
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
        or name.startswith("_debug_") or name.startswith("_qa_stage6e1_")
        or name.endswith(".pem") or "docker-volume" in parts
    )


def main() -> None:
    output = output_zip()
    reports = {p.name for p in (ROOT / "reports").iterdir() if p.is_file() and p.name.startswith("STAGE6E1_")}
    missing = REQUIRED_REPORTS - reports
    if missing:
        raise SystemExit(f"missing Stage 6E.1 reports: {sorted(missing)}")
    screenshots = {p.name for p in (ROOT / "reports/screenshots").glob("stage6e1_*.png")}
    missing_shots = REQUIRED_SCREENSHOTS - screenshots
    if missing_shots:
        raise SystemExit(f"missing Stage 6E.1 screenshots: {sorted(missing_shots)}")
    files = [p for p in ROOT.rglob("*") if p.is_file() and not forbidden(p.relative_to(ROOT))]
    if not any(p.name == ".env.example" for p in files):
        raise SystemExit(".env.example with placeholders must be packaged")
    if any(p.name == ".env" for p in files):
        raise SystemExit(".env must not be packaged")
    if output.exists():
        output.unlink()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(ROOT))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with ZipFile(output) as archive:
        bad = archive.testzip()
        names = archive.namelist()
    if bad:
        raise SystemExit(f"CRC failure: {bad}")
    if len({name.split("/")[0] for name in names}) != 1:
        raise SystemExit("archive has more than one root")
    if any(forbidden(Path(name)) for name in names):
        raise SystemExit("forbidden entry detected")
    if not any(Path(name).name == ".env.example" for name in names):
        raise SystemExit("archive is missing .env.example")
    if any(Path(name).name == ".env" for name in names):
        raise SystemExit("archive contains .env")
    print(f"OUTPUT_ZIP = {output}")
    print(f"ZIP_ENTRIES = {len(names)}")
    print(f"STAGE6E1_REPORTS = {len(REQUIRED_REPORTS)}")
    print(f"STAGE6E1_SCREENSHOTS = {len(REQUIRED_SCREENSHOTS)}")
    print(f"SHA256 = {digest}")
    print("CRC = PASS")
    print("ENV_EXAMPLE = present")
    print("DOTENV = absent")
    print("FORBIDDEN_ENTRIES = 0")


if __name__ == "__main__":
    main()
