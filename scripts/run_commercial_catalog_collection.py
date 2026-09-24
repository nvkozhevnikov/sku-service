"""Manual bounded Intervesp/Beka-Mak commercial collection; no scheduler."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.commercial_collection import (
    CandidateManifest, discover_commercial_candidates, run_commercial_collection,
    write_candidate_manifest, write_collection_report, write_review_report,
)
from universal_supplier.postgres import PostgresConfig, PostgresRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded read-only commercial collection; suppliers remain disabled.")
    parser.add_argument("--source", choices=("all", "intervesp", "beka_mak"), default="all")
    parser.add_argument("--limit", type=int, default=20, help="Maximum cards per site, 1..30")
    parser.add_argument("--offset", type=int, default=0,
                        help="Stable per-site candidate offset for a manual resumed batch")
    parser.add_argument("--discovery-only", action="store_true",
                        help="Fetch only bounded category pages and write a candidate manifest")
    parser.add_argument("--candidate-manifest-out", type=Path,
                        help="Required with --discovery-only; receives URLs and SHA-256 checksums")
    parser.add_argument("--review-report-out", type=Path,
                        help="Optional JSON destination for locally excluded REVIEW candidates")
    parser.add_argument("--expected-candidate-manifest", type=Path,
                        help="Require an identical manifest before any product capture")
    parser.add_argument("--pause-seconds", type=float, default=20.0, help="Minimum public pause is 20 seconds")
    parser.add_argument("--dry-run", action="store_true", help="Capture and parse only; do not connect or persist")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-sslmode", default="disable")
    parser.add_argument("--db-password-env", default="DB_PASSWORD")
    args = parser.parse_args()
    sites = ("intervesp", "beka_mak") if args.source == "all" else (args.source,)
    if args.discovery_only:
        if not args.candidate_manifest_out:
            parser.error("--discovery-only requires --candidate-manifest-out")
        if args.expected_candidate_manifest or not args.dry_run:
            parser.error("--discovery-only must not use DB options or an expected manifest")
        manifest, rows = discover_commercial_candidates(sites=sites, evidence_dir=args.evidence_dir,
                                                         pause_seconds=args.pause_seconds)
        write_candidate_manifest(manifest, args.candidate_manifest_out)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(__import__("json").dumps({"rows": [row.__dict__ for row in rows]}, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.review_report_out:
            write_review_report(rows, args.review_report_out)
        print(args.candidate_manifest_out)
        return
    repository = None
    expected_manifest = None
    if args.expected_candidate_manifest:
        expected_manifest = CandidateManifest.from_jsonable(__import__("json").loads(args.expected_candidate_manifest.read_text(encoding="utf-8")))
    if not args.dry_run:
        required = {"--db-host": args.db_host, "--db-port": args.db_port, "--db-name": args.db_name, "--db-user": args.db_user}
        missing = [key for key, value in required.items() if value in (None, "")]
        password = os.environ.get(args.db_password_env)
        if missing or not password:
            parser.error("non-dry-run requires explicit DB host/port/name/user and a password environment variable")
        repository = PostgresRepository(PostgresConfig(args.db_host, args.db_port, args.db_name, args.db_user, password, args.db_sslmode))
    try:
        result = run_commercial_collection(sites=sites, limit=args.limit, pause_seconds=args.pause_seconds,
                                           evidence_dir=args.evidence_dir, dry_run=args.dry_run, repository=repository,
                                           candidate_offset=args.offset, expected_manifest=expected_manifest)
        write_collection_report(result, args.report)
        if args.review_report_out:
            write_review_report(result.rows, args.review_report_out)
        print(args.report)
    finally:
        if repository:
            repository.close()


if __name__ == "__main__":
    main()
