#!/usr/bin/env python3
"""Create the self-contained Stage 4 checkpoint without runtime secrets."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE4_OFFER_SELECTION_ENGINE_CORRECTED2"
EXCLUDED_PARTS = {".git", ".pytest_cache", "__pycache__", "backups", ".venv", "venv"}
EXCLUDED_NAMES = {".env"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".dump", ".backup", ".key", ".pem", ".zip"}


def included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        not any(part in EXCLUDED_PARTS for part in relative.parts)
        and path.name not in EXCLUDED_NAMES
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = sorted(path for path in root.rglob("*") if path.is_file() and included(path, root))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in files:
            archive.write(path, (Path(ARCHIVE_ROOT) / path.relative_to(root)).as_posix())
    print(f"files={len(files)}")
    print(f"bytes={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
