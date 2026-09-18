#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT=Path(__file__).resolve().parents[1]
PACKAGE_NAME="UNIVERSAL_SUPPLIER_STAGE6E_OPERATOR_DATA_TOOLS_PROPERTIES"
OUTPUT=ROOT.parents[1]/f"{PACKAGE_NAME}.zip"
REQUIRED_REPORTS={
 "STAGE6E_SUMMARY.txt","STAGE6E_QA_REPORT.md","STAGE6E_EXPORT_QA.md","STAGE6E_DATA_QUALITY_QA.md",
 "STAGE6E_ANOMALY_GUARD_QA.md","STAGE6E_MISSING_PRODUCT_POLICY.md","STAGE6E_PARSER_INSPECTOR_QA.md",
 "STAGE6E_PROPERTY_REGISTRY.md","STAGE6E_PROPERTY_MAPPING_QA.md","STAGE6E_FILTER_PROFILES.md",
 "STAGE6E_FIELD_OWNERSHIP.md","STAGE6E_STERBRUST_PREVIEW_QA.md","STAGE6E_MIGRATION_QA.md",
}
REQUIRED_SCREENSHOTS={
 "stage6e_data_quality.png","stage6e_products_export.png","stage6e_change_history.png","stage6e_parser_inspector.png",
 "stage6e_rules_exclusions.png","stage6e_property_registry.png","stage6e_property_mapping_review.png",
 "stage6e_category_filter_profile.png","stage6e_sterbrust_readonly_preview.png",
}

def forbidden(path:Path)->bool:
    parts={part.lower() for part in path.parts}
    name=path.name.lower()
    return (name==".env" or "__pycache__" in parts or ".pytest_cache" in parts or "venv" in parts or ".venv" in parts
            or name.endswith(".pyc") or name.endswith(".pyo") or name.endswith(".dump") or name.endswith(".sql.gz")
            or "pgdata" in parts or name.startswith("test_export_") or name.endswith(".partial"))

def main():
    reports={p.name for p in (ROOT/"reports").iterdir() if p.is_file() and p.name.startswith("STAGE6E_")}
    screenshots={p.name for p in (ROOT/"reports/screenshots").glob("stage6e_*.png")}
    if reports != REQUIRED_REPORTS: raise SystemExit(f"Stage6E reports mismatch: missing={sorted(REQUIRED_REPORTS-reports)}, extra={sorted(reports-REQUIRED_REPORTS)}")
    if screenshots != REQUIRED_SCREENSHOTS: raise SystemExit(f"Stage6E screenshots mismatch: missing={sorted(REQUIRED_SCREENSHOTS-screenshots)}, extra={sorted(screenshots-REQUIRED_SCREENSHOTS)}")
    files=[p for p in ROOT.rglob("*") if p.is_file() and not forbidden(p.relative_to(ROOT))]
    if OUTPUT.exists(): OUTPUT.unlink()
    with ZipFile(OUTPUT,"w",ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(files,key=lambda p:p.relative_to(ROOT).as_posix()):
            archive.write(path,Path(PACKAGE_NAME)/path.relative_to(ROOT))
    digest=hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    with ZipFile(OUTPUT) as archive:
        bad=archive.testzip(); names=archive.namelist()
    if bad: raise SystemExit(f"CRC failure: {bad}")
    if len({name.split('/')[0] for name in names})!=1: raise SystemExit("archive has more than one root")
    if any(forbidden(Path(name)) for name in names): raise SystemExit("forbidden entry detected")
    print(f"OUTPUT_ZIP = {OUTPUT}")
    print(f"ZIP_ENTRIES = {len(names)}")
    print(f"STAGE6E_REPORTS = {len(reports)}")
    print(f"STAGE6E_SCREENSHOTS = {len(screenshots)}")
    print(f"SHA256 = {digest}")
    print("CRC = PASS")
    print("FORBIDDEN_ENTRIES = 0")

if __name__=="__main__": main()
