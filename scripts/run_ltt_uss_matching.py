"""Explicit future matching-only entrypoint. Default is read-only readiness plan.

No registry load, canonical bootstrap, NEW grouping, selection or supplier HTTP.
Applying requires both --apply and --enable-matching-writes. Never run as part
of source ingestion or its finalized checkpoint.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.identity_service import _load_cards, reconcile_matching_only
from universal_supplier.postgres import PostgresConfig

SOURCES = ('ltt_msk', 'ltt_sib', 'ltt_yug', 'uss_stanko')
EXPECTED_SCOPE = dict(ltt_msk=365, ltt_sib=1266, ltt_yug=591, uss_stanko=227)


def plan(connection, sources=SOURCES):
    """SELECT-only readiness inventory; deliberately never calls the matcher."""
    if not sources or not set(sources).issubset(SOURCES):
        raise ValueError('Unsupported supplier namespace')
    counts = {}
    for code in sources:
        cards, _ = _load_cards(connection, code)
        counts[code] = len(cards)
    registry = connection.execute(
        'SELECT count(*),count(*) FILTER (WHERE catalog_product_id IS NULL) FROM sterbrust_products WHERE active IS DISTINCT FROM false'
    ).fetchone()
    return dict(mode='READ_ONLY_PLAN', sources=counts, registry_active=registry[0],
                missing_catalog_links=registry[1], registry_ready=bool(registry[0] and not registry[1]),
                scope_ready=all(counts[c] == EXPECTED_SCOPE[c] for c in counts),
                matching_executed=False, offer_selection_executed=False, db_writes=0)


def execute(config, *, apply=False, enable_matching_writes=False, sources=SOURCES):
    if not sources or not set(sources).issubset(SOURCES):
        raise ValueError('Unsupported supplier namespace')
    if apply and not enable_matching_writes:
        raise ValueError('--apply requires --enable-matching-writes')
    if config.host != '127.0.0.1' or config.port != 55455 or config.dbname != 'universal_supplier_ltt_uss':
        raise ValueError('Only the explicitly isolated LTT/USS target is supported')
    import psycopg
    with psycopg.connect(**config.kwargs(), autocommit=True,
                         options='-c default_transaction_read_only=on') as connection:
        readiness = plan(connection, sources)
    if not apply:
        return readiness
    if not readiness['registry_ready'] or not readiness['scope_ready']:
        raise RuntimeError('REGISTRY_OR_SCOPE_NOT_READY')
    return {code: reconcile_matching_only(config, code) for code in sources}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--enable-matching-writes', action='store_true')
    parser.add_argument('--supplier', choices=SOURCES, action='append')
    args = parser.parse_args(argv)
    if args.apply and not args.enable_matching_writes:
        parser.error('--apply requires --enable-matching-writes')
    result = execute(PostgresConfig.from_env(), apply=args.apply,
                     enable_matching_writes=args.enable_matching_writes,
                     sources=tuple(dict.fromkeys(args.supplier or SOURCES)))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
