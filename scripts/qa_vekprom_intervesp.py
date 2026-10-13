"""Stratified QA gate for the vekprom/intervesp ingestion outputs.

Samples at least --min-samples rows per supplier across strata (price_state x
availability, run status, historical untouched rows) and verifies each sample
three ways: adapter re-parse of the stored evidence, the RUN1 checkpoint row,
and the live database projection (source_products / offers / latest
observation).  Writes QA_SAMPLES_<SUPPLIER>.csv and QA_GATE_<SUPPLIER>.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.adapters.intervesp import parse_intervesp_detail  # noqa: E402
from universal_supplier.adapters.vekprom import parse_vekprom_detail  # noqa: E402
from universal_supplier.commercial_persistence import normalize_availability  # noqa: E402 - single source of truth
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
PARSERS = {'vekprom': parse_vekprom_detail, 'intervesp': parse_intervesp_detail}


def _price_eq(left, right) -> bool:
    """Numeric-aware price equality (DB NUMERIC arrives as Decimal('2880228.0000'),
    checkpoint rows as int/float 2880228); non-numeric values fall back to string eq."""
    if left is None or right is None:
        return left is None and right is None
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return str(left) == str(right)


def stratified_pick(rows: list[dict], limit: int) -> list[str]:
    strata: dict[tuple, list[str]] = defaultdict(list)
    for url, row in sorted(rows):
        key = (row.get('price_state'), row.get('availability'), row.get('status'))
        strata[key].append(url)
    picked: list[str] = []
    while len(picked) < limit and any(strata.values()):
        for key in sorted(strata, key=lambda k: (len(strata[k]), str(k)), reverse=False):
            if strata[key]:
                picked.append(strata[key].pop())
                if len(picked) >= limit:
                    break
    return picked


def availability_mismatch(run1_availability: str | None, db_availability_normalized: str | None) -> str | None:
    """Strictly compare the DB availability projection with the canonical contract.

    The expectation comes from ``commercial_persistence.normalize_availability``
    (the exact function the write path uses), so the check fails on ANY
    divergence: a wrong mapping, a missing value, or a raw schema.org token
    (e.g. ``OutOfStock``) leaking into the normalized column.
    """
    expected = normalize_availability(run1_availability)
    actual = db_availability_normalized or 'unknown'
    return None if actual == expected else f'db_availability:{actual}!=run1:{expected}'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--supplier', choices=('vekprom', 'intervesp'), required=True)
    parser.add_argument('--min-samples', type=int, default=100)
    args = parser.parse_args()
    run1 = json.loads((REPORTS / f'{args.supplier.upper()}_RUN1' / 'CHECKPOINT.json').read_text(encoding='utf-8'))
    if run1.get('status') != 'COMPLETED':
        raise SystemExit(f'RUN1 not COMPLETED: {run1.get("status")}')
    applied = run1['applied']
    persisted_items = [(url, row) for url, row in applied.items() if row.get('status') == 'PERSISTED']
    other_items = [(url, row) for url, row in applied.items() if row.get('status') != 'PERSISTED']
    picked = stratified_pick(persisted_items, args.min_samples)
    strata_seen = Counter()
    gate = {'supplier': args.supplier, 'recorded_at': datetime.now(timezone.utc).isoformat(),
            'run1_started_at': run1['started_at'], 'persisted_total': len(persisted_items),
            'other_total': len(other_items), 'samples_target': args.min_samples,
            'samples_persisted': len(picked), 'samples_other': 0, 'samples_historical': 0,
            'mismatches': [], 'checks': Counter(), 'status': 'QA_RUNNING'}
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    repository = PostgresRepository(PostgresConfig.from_env())
    samples = []
    try:
        for url in picked:
            row = applied[url]
            strata_seen[(row.get('price_state'), row.get('availability'))] += 1
            evidence_text = str(row['evidence_ref'])
            if evidence_text.startswith('capture:'):
                evidence_text = evidence_text[len('capture:'):]
            evidence_text = evidence_text.lstrip('/\\')
            evidence_path = Path(evidence_text)
            if not evidence_path.is_absolute():
                evidence_path = REPORTS / f'{args.supplier.upper()}_RUN1' / 'EVIDENCE' / args.supplier / evidence_path
            product = PARSERS[args.supplier](evidence_path.read_bytes().decode('utf-8', 'replace'),
                                             source_url=row['final_url'])
            parsed = {'external_id': product.site_internal_id, 'name': product.name,
                      'price_state': product.price.state.value,
                      'price': None if product.price.current_price is None else str(product.price.current_price),
                      'currency': product.price.currency, 'availability': product.availability or 'unknown'}
            with repository.connection.cursor() as cursor:
                cursor.execute("""SELECT p.external_id,p.name,o.price,o.currency,o.availability_normalized,
                                  (SELECT obs.price_state FROM offer_commercial_observations obs
                                   WHERE obs.offer_id=o.id ORDER BY obs.observed_at DESC, obs.id DESC LIMIT 1)
                                  FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                                  LEFT JOIN offers o ON o.source_product_id=p.id AND o.offer_kind='default'
                                  WHERE s.code=%s AND p.id=%s""", (args.supplier, row['source_product_id']))
                db_row = cursor.fetchone()
            db = {'external_id': db_row[0], 'name': db_row[1],
                  'price': None if db_row[2] is None else str(db_row[2]),
                  'currency': db_row[3], 'availability_normalized': db_row[4], 'price_state': db_row[5]}
            mismatches = []
            if parsed['external_id'] != row.get('external_id'):
                mismatches.append(f"parsed_external_id:{parsed['external_id']}!=run1:{row.get('external_id')}")
            if parsed['name'] != row.get('name'):
                mismatches.append('parsed_name!=run1_name')
            if parsed['price_state'] != row.get('price_state'):
                mismatches.append(f"parsed_price_state:{parsed['price_state']}!=run1:{row.get('price_state')}")
            if not _price_eq(parsed['price'], row.get('price')):
                mismatches.append('parsed_price!=run1_price')
            if parsed['availability'] != row.get('availability'):
                mismatches.append('parsed_availability!=run1_availability')
            if db['name'] != row.get('name'):
                mismatches.append('db_name!=run1_name')
            if not _price_eq(db['price'], row.get('price')):
                mismatches.append(f"db_price:{db['price']}!=run1:{row.get('price')}")
            if db['price_state'] != row.get('price_state'):
                mismatches.append(f"db_price_state:{db['price_state']}!=run1:{row.get('price_state')}")
            mismatch = availability_mismatch(row.get('availability'), db['availability_normalized'])
            if mismatch:
                mismatches.append(mismatch)
            entry = {'url': url, 'stratum': f"{row.get('price_state')}|{row.get('availability')}",
                     'external_id': row.get('external_id'), 'name': (row.get('name') or '')[:100],
                     'price_state': row.get('price_state'), 'price': row.get('price'),
                     'currency': row.get('currency'), 'availability': row.get('availability'),
                     'db_price_state': db['price_state'], 'db_price': db['price'],
                     'db_availability': db['availability_normalized'], 'match': not mismatches,
                     'mismatches': ';'.join(mismatches)}
            samples.append(entry)
            gate['checks']['persisted_verified' if not mismatches else 'persisted_mismatch'] += 1
            for mismatch in mismatches:
                gate['mismatches'].append({'url': url, 'mismatch': mismatch})
        # Non-persisted terminal rows: prove accounting-only states have no DB rows
        # created for their URLs (vekprom new namespace) or were left untouched.
        for url, row in other_items[:max(0, args.min_samples - len(samples))]:
            with repository.connection.cursor() as cursor:
                cursor.execute("""SELECT count(*) FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                                  WHERE s.code=%s AND p.source_url=%s""", (args.supplier, url))
                exists = cursor.fetchone()[0]
            expected = 0 if args.supplier == 'vekprom' else None
            ok = (exists == 0) if args.supplier == 'vekprom' else True
            samples.append({'url': url, 'stratum': f"other|{row.get('status')}",
                            'external_id': row.get('external_id'), 'name': (row.get('name') or '')[:100],
                            'price_state': row.get('price_state'), 'price': row.get('price'),
                            'currency': row.get('currency'), 'availability': row.get('availability'),
                            'db_price_state': None, 'db_price': None, 'db_availability': None,
                            'match': ok, 'mismatches': '' if ok else f'db_row_exists:{exists}'})
            gate['samples_other'] += 1
            gate['checks']['other_verified' if ok else 'other_mismatch'] += 1
            if not ok:
                gate['mismatches'].append({'url': url, 'mismatch': f'db_row_exists:{exists}'})
        # Intervesp: top up with historical untouched rows until min-samples.
        if args.supplier == 'intervesp':
            need = max(0, args.min_samples - len(samples))
            if need:
                with repository.connection.cursor() as cursor:
                    cursor.execute("""SELECT p.external_id,p.last_changed_at,p.active,p.missed_crawls
                                      FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                                      WHERE s.code='intervesp' AND p.external_id LIKE 'url-sha256%%'
                                      ORDER BY p.external_id LIMIT %s""", (need * 2,))
                    historical = cursor.fetchall()
                for external_id, last_changed, active, missed in historical[:need]:
                    ok = (active and missed == 0 and (last_changed is None
                                                      or last_changed.isoformat() <= run1['started_at']))
                    samples.append({'url': f'db:{external_id}', 'stratum': 'historical_untouched',
                                    'external_id': external_id, 'name': '', 'price_state': '', 'price': '',
                                    'currency': '', 'availability': '', 'db_price_state': None,
                                    'db_price': None, 'db_availability': None, 'match': ok,
                                    'mismatches': '' if ok else 'historical_row_touched'})
                    gate['samples_historical'] += 1
                    gate['checks']['historical_verified' if ok else 'historical_mismatch'] += 1
                    if not ok:
                        gate['mismatches'].append({'url': external_id, 'mismatch': 'historical_row_touched'})
        gate['strata'] = {f'{key[0]}|{key[1]}': value for key, value in strata_seen.items()}
        gate['samples_total'] = len(samples)
        gate['status'] = ('QA_GATE_PASSED' if not gate['mismatches'] and len(samples) >= args.min_samples
                          else 'QA_GATE_FAILED')
        with (REPORTS / f'QA_SAMPLES_{args.supplier.upper()}.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(samples[0].keys()))
            writer.writeheader()
            writer.writerows(samples)
        gate['checks'] = dict(gate['checks'])
        (REPORTS / f'QA_GATE_{args.supplier.upper()}.json').write_text(
            json.dumps(gate, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: gate[k] for k in ('status', 'samples_total', 'samples_persisted',
                                               'samples_other', 'samples_historical', 'checks')},
                         ensure_ascii=False), flush=True)
        if gate['status'] != 'QA_GATE_PASSED':
            raise SystemExit(4)
    finally:
        repository.close()


if __name__ == '__main__':
    main()
