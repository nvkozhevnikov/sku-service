"""Read-only audit of sanitized Stage4 capture files before consolidation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import psycopg


EXPECTED_SYSTEM_ID = 7689025282387590508
REF_RE = re.compile(r"^capture://[^/]+/([0-9a-f]{64}\.html)$")


def audit(repo_root: Path) -> dict:
    report_root = repo_root / "reports"
    files: dict[str, list[Path]] = {}
    for current, directories, names in os.walk(report_root):
        directories[:] = [name for name in directories
                          if "cold_backup" not in name and not name.startswith("pytest_tmp")]
        for name in names:
            if re.fullmatch(r"[0-9a-f]{64}\.html", name):
                files.setdefault(name, []).append(Path(current) / name)
    with psycopg.connect(host="127.0.0.1", port=55447, dbname="stage4_commercial_qa",
                         user="stage4_runtime", options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            identity = tuple(cursor.fetchone())
            if identity != ("stage4_commercial_qa", "stage4_runtime", "17.11", False, EXPECTED_SYSTEM_ID):
                raise RuntimeError("Stage4 identity changed")
            cursor.execute("SELECT id,evidence_ref,evidence_sha256 FROM supplier_http_captures ORDER BY id")
            captures = cursor.fetchall()
    missing = []
    corrupt = []
    resolved = 0
    for capture_id, ref, expected_sha in captures:
        match = REF_RE.fullmatch(ref)
        if match is None:
            missing.append({"capture_id": capture_id, "evidence_ref": ref, "reason": "invalid_ref"})
            continue
        candidates = files.get(match.group(1), [])
        if not candidates:
            missing.append({"capture_id": capture_id, "evidence_ref": ref, "reason": "file_missing"})
            continue
        if any(hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha for path in candidates):
            resolved += 1
        else:
            corrupt.append({"capture_id": capture_id, "evidence_ref": ref, "reason": "checksum_mismatch"})
    return {"evidence_level": "LIVE_SQL_AND_LOCAL_FILE_HASH", "system_identifier": EXPECTED_SYSTEM_ID,
            "captures": len(captures), "resolved": resolved, "missing_count": len(missing),
            "corrupt_count": len(corrupt), "missing": missing, "corrupt": corrupt}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.repo_root)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
