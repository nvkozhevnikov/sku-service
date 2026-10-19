"""Read-only A/B evaluation of strict model/article keys against a PostgreSQL database.

Usage (DB_* environment as for every other tool; RC is safe, nothing is written):

    python scripts/evaluate_strict_identity_keys.py --out <new directory> [--supplier CODE]

The connection is opened with default_transaction_read_only=on, so any write
attempt fails in PostgreSQL. Two passes are evaluated:

* with_existing_links - what the next normal reconcile would decide;
* fresh - matching from scratch, without current links.

Output: SUMMARY.json and CHANGED_<pass>.csv (only cards whose decision differs).
The output directory must not exist yet; earlier evidence is never overwritten.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_supplier.identity_service import _load_cards  # noqa: E402
from universal_supplier.matching_service import load_registry_from_postgres  # noqa: E402
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402
from universal_supplier.strict_key_evaluation import compare, summarize  # noqa: E402


def read_only_connection(config: PostgresConfig):
    import psycopg
    connection = psycopg.connect(**config.kwargs(), autocommit=True, options="-c default_transaction_read_only=on")
    with connection.cursor() as cursor:
        cursor.execute("SHOW default_transaction_read_only")
        if cursor.fetchone()[0] != "on":
            raise RuntimeError("connection is not read-only; refusing to continue")
    return connection


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(rows[0]) if rows else ["transition"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="new output directory")
    parser.add_argument("--supplier", help="limit to one supplier code")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"{args.out} already exists; choose a new directory")
    connection = read_only_connection(PostgresConfig.from_env())
    try:
        registry = load_registry_from_postgres(connection)
        cards, _ = _load_cards(connection, args.supplier)
        grouped: dict[str, list] = {}
        for card in cards:
            grouped.setdefault(card.supplier_code, []).append(card)
        repository = SimpleNamespace(connection=connection)
        passes = {"with_existing_links": [], "fresh": []}
        for code, supplier_cards in sorted(grouped.items()):
            existing = PostgresRepository.load_existing_link_contexts(repository, code)
            passes["with_existing_links"] += compare(supplier_cards, registry, existing)
            passes["fresh"] += compare(supplier_cards, registry, {})
    finally:
        connection.close()
    args.out.mkdir(parents=True)
    summary = {"registry_products": len(registry.products), **{name: summarize(rows) for name, rows in passes.items()}}
    (args.out / "SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, rows in passes.items():
        write_csv(args.out / f"CHANGED_{name}.csv", [row for row in rows if row["transition"] != "UNCHANGED"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
