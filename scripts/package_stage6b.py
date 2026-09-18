from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parents[2] / "UNIVERSAL_SUPPLIER_STAGE6B_PRODUCT_IDENTITY_HARDENING.zip"
SIDECAR = OUTPUT.with_suffix(".sha256")
MANIFEST = ROOT / "reports" / "STAGE6B_PACKAGE_MANIFEST.json"

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".uv-cache-stage6b", "node_modules", "docker-volumes", "pgdata",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".dump", ".backup", ".bak", ".zip"}
SENSITIVE_NAMES = re.compile(r"(^|[._-])(password|passwd|token|secret|credential|private[_-]?key)([._-]|$)", re.I)


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS or part.startswith("pytest_") or part.startswith(".pytest_") for part in relative.parts[:-1]):
        return False
    if path.name == ".env" or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if SENSITIVE_NAMES.search(path.name):
        return False
    return path.is_file()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def collect() -> list[Path]:
    return sorted((path for path in ROOT.rglob("*") if included(path) and path != MANIFEST), key=lambda p: p.as_posix())


def main() -> None:
    files = collect()
    prohibited = [str(path.relative_to(ROOT)) for path in files if path.name == ".env" or SENSITIVE_NAMES.search(path.name)]
    if prohibited:
        raise SystemExit(f"Prohibited files: {prohibited}")
    manifest = {
        "checkpoint": ROOT.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_checkpoint": "UNIVERSAL_SUPPLIER_STAGE6_STERBRUST_IMPORT_CONTRACT_DRY_RUN.zip",
        "source_checkpoint_sha256": "749712b181bda3d00ca6360af5047438600865385589036e6f55cf1445b33832",
        "stage6b": "CLOSED",
        "stage6": "BLOCKED_IMPORT_CONTRACT_EVIDENCE",
        "sterbrust_writes": 0,
        "esol_import_runs": 0,
        "products_created_on_sterbrust": 0,
        "excluded": [".env", "credentials", "secrets", "DB dumps", "Docker volume data", "venv", "cache", "*.zip"],
        "files": [{"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path), "size": path.stat().st_size} for path in files],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = collect() + [MANIFEST]
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda p: p.as_posix()):
            archive.write(path, (Path(ROOT.name) / path.relative_to(ROOT)).as_posix())
    archive_hash = digest(OUTPUT)
    SIDECAR.write_text(f"{archive_hash}  {OUTPUT.name}\n", encoding="ascii")
    with zipfile.ZipFile(OUTPUT) as archive:
        names = archive.namelist()
        bad = [name for name in names if any(part in EXCLUDED_DIRS or part.startswith("pytest_") or part.startswith(".pytest_") for part in Path(name).parts) or Path(name).name == ".env" or Path(name).suffix.lower() in EXCLUDED_SUFFIXES]
        if bad:
            raise SystemExit(f"Archive exclusion failure: {bad[:10]}")
        corrupt = archive.testzip()
        if corrupt:
            raise SystemExit(f"Corrupt archive member: {corrupt}")
    print(f"CHECKPOINT={OUTPUT}")
    print(f"SHA256={archive_hash}")
    print(f"FILES={len(files)}")
    print(f"BYTES={OUTPUT.stat().st_size}")
    print("EXCLUSION_QA=PASS")
    print("ZIP_INTEGRITY=PASS")


if __name__ == "__main__":
    main()
