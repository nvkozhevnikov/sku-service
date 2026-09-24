"""Manual bounded Intervesp/Beka-Mak commercial collection; no scheduler."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.commercial_collection import run_commercial_collection, write_collection_report
from universal_supplier.postgres import PostgresConfig, PostgresRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded read-only commercial collection; suppliers remain disabled.")
    parser.add_argument("--source", choices=("all", "intervesp", "beka_mak"), default="all")
    parser.add_argument("--limit", type=int, default=20, help="Maximum cards per site, 1..30")
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
    repository = None
    if not args.dry_run:
        required = {"--db-host": args.db_host, "--db-port": args.db_port, "--db-name": args.db_name, "--db-user": args.db_user}
        missing = [key for key, value in required.items() if value in (None, "")]
        password = os.environ.get(args.db_password_env)
        if missing or not password:
            parser.error("non-dry-run requires explicit DB host/port/name/user and a password environment variable")
        repository = PostgresRepository(PostgresConfig(args.db_host, args.db_port, args.db_name, args.db_user, password, args.db_sslmode))
    try:
        result = run_commercial_collection(sites=sites, limit=args.limit, pause_seconds=args.pause_seconds,
                                           evidence_dir=args.evidence_dir, dry_run=args.dry_run, repository=repository)
        write_collection_report(result, args.report)
        print(args.report)
    finally:
        if repository:
            repository.close()


if __name__ == "__main__":
    main()
