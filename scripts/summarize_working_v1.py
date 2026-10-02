"""Summarize pinned RUN1/RUN2 evidence and a read-only SQL snapshot."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.full_supplier_collection import FullManifest
from universal_supplier.proposal_export import capture_review_rows


SOURCES = ("intervesp", "beka_mak", "beka_mak_tr")


def summarize(manifest: FullManifest, reports: list[dict], qa: dict, matching: dict) -> dict:
    allowed = {(item.source, item.url) for item in manifest.candidates}
    if any(report.get("manifest_sha256") != manifest.sha256 for report in reports):
        raise ValueError("a run report has a different manifest SHA-256")
    latest = {}
    for report in reports:
        for row in report.get("rows", []):
            key = (row.get("source"), row.get("url"))
            if key not in allowed:
                raise ValueError("a run report contains an unapproved candidate URL")
            latest[key] = row
    by_source = {}
    for source in SOURCES:
        rows = [row for (site, _), row in latest.items() if site == source]
        status = Counter(str(row.get("status") or "") for row in rows)
        price_states = Counter(str(row.get("price_state") or "") for row in rows
                               if row.get("status") == "PERSISTED")
        by_source[source] = {
            "manifest_candidates": sum(item.source == source for item in manifest.candidates),
            "latest_attempted": len(rows), "latest_statuses": dict(sorted(status.items())),
            "latest_persisted_price_states": dict(sorted(price_states.items())),
            "sql": qa["by_source"][source],
            "sql_current_price_states": qa["current_price_states"][source],
        }
    capture_reviews = capture_review_rows(allowed_urls=allowed,
                                           discovery_reviews=list(manifest.review_urls), reports=reports)
    return {"manifest_sha256": manifest.sha256,
            "qa_identity": qa["identity"], "sql_counts": qa["counts"],
            "sql_safety_checks": qa["checks"], "suppliers_enabled": qa["suppliers_enabled"],
            "by_source": by_source, "discovery_reviews": len(manifest.review_urls),
            "unresolved_capture_reviews": len(capture_reviews),
            "matching_summary": matching["summary"],
            "report_runs": [{"started_at": report.get("started_at"),
                             "finished_at": report.get("finished_at"),
                             "selected_count": report.get("selected_count"),
                             "attempted": len(report.get("rows", [])),
                             "observations_created": sum(bool(row.get("observation_created"))
                                                         for row in report.get("rows", [])),
                             "exact_noops": sum(bool(row.get("exact_noop"))
                                                for row in report.get("rows", [])),
                             "aborted_reason": report.get("aborted_reason")}
                            for report in reports]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", action="append", required=True, type=Path)
    parser.add_argument("--qa-snapshot", required=True, type=Path)
    parser.add_argument("--matching", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = FullManifest.from_jsonable(json.loads(args.manifest.read_text(encoding="utf-8")))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.report]
    qa = json.loads(args.qa_snapshot.read_text(encoding="utf-8"))
    matching = json.loads(args.matching.read_text(encoding="utf-8"))
    if matching.get("manifest_sha256") != manifest.sha256:
        raise ValueError("matching manifest SHA-256 changed")
    result = summarize(manifest, reports, qa, matching)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "by_source": result["by_source"],
                      "sql_counts": result["sql_counts"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
