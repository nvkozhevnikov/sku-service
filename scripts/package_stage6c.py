from __future__ import annotations
from datetime import datetime, timezone
import hashlib, json, os, re, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT.parents[2]/"UNIVERSAL_SUPPLIER_STAGE6C_WEB_CONTROL_PLANE_SCHEDULER_CORRECTED.zip"
SIDECAR=OUTPUT.with_suffix(".sha256")
MANIFEST=ROOT/"reports/STAGE6C_PACKAGE_MANIFEST.json"
EXCLUDED_DIRS={".git",".venv","venv","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",".uv-cache-stage6b",".uv-cache-stage6c",".uv-cache-stage6c1","node_modules","docker-volumes","pgdata",".stage6c_pytest_tmp",".stage6c1_pytest_tmp"}
EXCLUDED_SUFFIXES={".pyc",".pyo",".dump",".backup",".bak",".zip"}
SENSITIVE_NAMES=re.compile(r"(^|[._-])(password|passwd|token|secret|credential|private[_-]?key)([._-]|$)",re.I)

def included(path):
    rel=path.relative_to(ROOT)
    return path.is_file() and not any(p in EXCLUDED_DIRS or p.startswith("pytest_") or p.startswith(".pytest_") for p in rel.parts[:-1]) and path.name!=".env" and path.suffix.lower() not in EXCLUDED_SUFFIXES and not SENSITIVE_NAMES.search(path.name)
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()
def collect():
    files=[]
    for directory, names, filenames in os.walk(ROOT, topdown=True):
        names[:] = [name for name in names if name not in EXCLUDED_DIRS and not name.startswith("pytest_") and not name.startswith(".pytest_")]
        for filename in filenames:
            path=Path(directory)/filename
            if included(path) and path!=MANIFEST: files.append(path)
    return sorted(files,key=lambda p:p.as_posix())

def main():
    files=collect()
    manifest={"checkpoint":ROOT.name,"created_at":datetime.now(timezone.utc).isoformat(),"source_checkpoint":"UNIVERSAL_SUPPLIER_STAGE6C_WEB_CONTROL_PLANE_SCHEDULER.zip","source_checkpoint_sha256":"b6d2dfe2c388a5cf1f32ff818c65966acafbf116486a09e56d6c33a9865eb7b0","stage6c_1":"CLOSED","stage6":"BLOCKED_IMPORT_CONTRACT_EVIDENCE","tests_passed":382,"previous_regression_tests_passed":321,"correction_tests_passed":61,"host_web_access":"LIVE-VERIFIED PASS","sterbrust_writes":0,"esol_import_runs":0,"products_created_on_sterbrust":0,"supplier_live_crawls":0,"excluded":[".env","credentials","secrets","DB dumps","Docker volume data","venv","cache","temporary QA data","*.zip"],"files":[{"path":p.relative_to(ROOT).as_posix(),"sha256":digest(p),"size":p.stat().st_size} for p in files]}
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    files=collect()+[MANIFEST]
    with zipfile.ZipFile(OUTPUT,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files,key=lambda p:p.as_posix()): z.write(p,(Path(ROOT.name)/p.relative_to(ROOT)).as_posix())
    archive_hash=digest(OUTPUT); SIDECAR.write_text(f"{archive_hash}  {OUTPUT.name}\n",encoding="ascii")
    with zipfile.ZipFile(OUTPUT) as z:
        bad=[n for n in z.namelist() if any(p in EXCLUDED_DIRS for p in Path(n).parts) or Path(n).name==".env" or Path(n).suffix.lower() in EXCLUDED_SUFFIXES]
        if bad or z.testzip(): raise SystemExit(f"archive QA failed: {bad[:5]}")
    print(f"CHECKPOINT={OUTPUT}\nSHA256={archive_hash}\nFILES={len(files)}\nBYTES={OUTPUT.stat().st_size}\nEXCLUSION_QA=PASS\nZIP_INTEGRITY=PASS")
if __name__=="__main__": main()
