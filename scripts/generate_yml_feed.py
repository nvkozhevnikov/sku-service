#!/usr/bin/env python3
"""Build the deterministic full Stage 5 YML snapshot from PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.postgres import PostgresConfig
from universal_supplier.yml_feed import (
    FeedPolicy,
    PostgresFeedStore,
    atomic_replace_validated,
    build_yml,
    validate_yml_bytes,
    write_core_reports,
)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the full selected-offer YML snapshot; never uploads it"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="query, build and validate without files or feed_runs writes")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "exports" / "sterbrust_selected_offers.xml")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "yml_feed.json")
    args = parser.parse_args()

    if not args.dry_run and os.environ.get("STAGE5_DB_CONFIRM") != "YES":
        raise SystemExit("Generation requires STAGE5_DB_CONFIRM=YES (feed_runs metadata only)")

    import psycopg
    policy = FeedPolicy.load(args.policy)
    connection = psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True)
    store = PostgresFeedStore(connection)
    run_id: int | None = None
    try:
        if not args.dry_run:
            run_id = store.start_feed_run(file_path=_display_path(args.output), policy=policy)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot = store.load_snapshot()
        artifact = build_yml(snapshot, policy)
        if not args.dry_run:
            atomic_replace_validated(
                args.output,
                artifact.data,
                lambda data: validate_yml_bytes(
                    data, expected_rows=snapshot.selected_rows, policy=policy
                ),
            )
            write_core_reports(artifact, snapshot, args.reports_dir)
            assert run_id is not None
            store.finish_feed_run(run_id, artifact)
        print(json.dumps({
            "result": "PASS",
            "mode": "dry-run" if args.dry_run else "generate",
            "feed_policy_version": policy.version,
            "selection_policy_version": artifact.manifest["selection_policy_version"],
            "db_selected_count": artifact.manifest["db_selected_count"],
            "feed_exported_count": artifact.manifest["exported_offer_count"],
            "not_managed_count": len(snapshot.not_managed_rows),
            "availability_counts": artifact.manifest["availability_counts"],
            "available_true_count": artifact.manifest["available_true_count"],
            "available_false_count": artifact.manifest["available_false_count"],
            "sha256": artifact.sha256,
            "feed_size_bytes": len(artifact.data),
            "output_replaced": not args.dry_run,
            "feed_run_id": run_id,
            "sterbrust_write_methods_used": 0,
            "sterbrust_upload": "NO",
            "cron_configured": "NO",
        }, ensure_ascii=False, indent=2, sort_keys=True))
    except Exception as exc:
        if run_id is not None:
            try:
                store.fail_feed_run(run_id, str(exc))
            except Exception as record_error:
                raise RuntimeError(f"{exc}; failed feed_run update: {record_error}") from exc
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
