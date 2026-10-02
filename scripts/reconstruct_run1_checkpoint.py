"""Read-only RUN1 ledger reconstruction from a pinned manifest and local reports.

This proves report continuity only. A fresh SQL identity/membership preflight is
required before any resumed write; this script never contacts a database/site.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_supplier.full_supplier_collection import FullManifest


TERMINAL = frozenset({"PERSISTED", "REVIEW", "AMBIGUOUS"})
SOURCES = ("intervesp", "beka_mak", "beka_mak_tr")


def reconstruct(manifest_path: Path, report_paths: list[Path]) -> dict:
    manifest = FullManifest.from_jsonable(json.loads(manifest_path.read_text(encoding="utf-8")))
    expected = {(item.source, item.url): item for item in manifest.candidates}
    latest: dict[tuple[str, str], dict] = {}
    reports: list[str] = []
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("manifest_sha256") != manifest.sha256 or report.get("dry_run") is not False:
            raise ValueError(f"RUN1 report has wrong manifest or is dry-run: {path}")
        reports.append(str(path))
        for row in report.get("rows", []):
            key = (row.get("source"), row.get("url"))
            item = expected.get(key)
            if item is None or row.get("expected_model") != item.expected_model:
                raise ValueError(f"RUN1 report contains an unexpected candidate: {path}")
            previous = latest.get(key)
            if previous and previous.get("status") in TERMINAL:
                raise ValueError(f"RUN1 reports repeat a completed candidate: {key}")
            latest[key] = row
    result = {"manifest_sha256": manifest.sha256, "evidence_level": "REPORT_ONLY_SQL_NOT_RECHECKED",
              "reports": reports, "sources": {}}
    for source in SOURCES:
        candidates = [item for item in manifest.candidates if item.source == source]
        statuses = [str(latest.get((source, item.url), {}).get("status") or "NOT_ATTEMPTED")
                    for item in candidates]
        first_pending = next((index for index, status in enumerate(statuses) if status not in TERMINAL), len(statuses))
        if any(status in TERMINAL for status in statuses[first_pending:]):
            raise ValueError(f"RUN1 has a completed item after a gap: {source}")
        result["sources"][source] = {
            "manifest_candidates": len(candidates), "terminal_prefix": first_pending,
            "first_unprocessed_offset": first_pending if first_pending < len(candidates) else None,
            "first_unprocessed_url": candidates[first_pending].url if first_pending < len(candidates) else None,
            "statuses": dict(sorted(Counter(statuses).items())),
            "run1_report_complete": first_pending == len(candidates),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = reconstruct(args.manifest, args.report)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
