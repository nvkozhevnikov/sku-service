"""Intervesp OLD vs NEW reconciliation after the authorized live refresh.

Classifies every baseline row (from the pre-run audit snapshot) into
REDISCOVERED / REDISCOVERED_UPDATED / REDISCOVERED_UNCHANGED / NEW /
HISTORICAL_NOT_REDISCOVERED and proves the untouched rows keep their baseline
commercial values (one healthy crawl miss never deactivates anything).
Writes INTERVESP_OLD_VS_NEW.json plus a full per-row CSV ledger.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'


def main() -> None:
    run1 = json.loads((REPORTS / 'INTERVESP_RUN1' / 'CHECKPOINT.json').read_text(encoding='utf-8'))
    if run1.get('status') != 'COMPLETED':
        raise SystemExit(f'INTERVESP RUN1 not COMPLETED: {run1.get("status")}')
    persisted = {row.get('external_id'): row for row in run1['applied'].values()
                 if row.get('status') == 'PERSISTED'}
    baseline = json.loads((REPORTS / 'INTERVESP_BASELINE_AUDIT.json').read_text(encoding='utf-8'))
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    repository = PostgresRepository(PostgresConfig.from_env())
    ledger = []
    summary = {'recorded_at': datetime.now(timezone.utc).isoformat(),
               'run1_started_at': run1['started_at'],
               'baseline_rows': baseline['rows'], 'run1_persisted': len(persisted),
               'REDISCOVERED': 0, 'REDISCOVERED_UPDATED': 0, 'REDISCOVERED_UNCHANGED': 0,
               'NEW': 0, 'HISTORICAL_NOT_REDISCOVERED': 0,
               'historical_active': 0, 'historical_deactivated': 0,
               'historical_touched_after_run1_started': 0}
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute("""SELECT p.external_id,p.source_url,p.name,o.price,o.currency,
                              o.availability_normalized,p.active,p.missed_crawls,p.last_success_at,
                              p.last_changed_at
                              FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                              LEFT JOIN offers o ON o.source_product_id=p.id AND o.offer_kind='default'
                              WHERE s.code='intervesp' ORDER BY p.external_id""")
            rows = cursor.fetchall()
    finally:
        repository.close()
    for (external_id, source_url, name, price, currency, availability, active,
         missed_crawls, last_success, last_changed) in rows:
        entry = {'external_id': str(external_id), 'source_url': source_url,
                 'db_price': None if price is None else str(price), 'db_currency': currency,
                 'db_availability': availability, 'active': bool(active),
                 'missed_crawls': missed_crawls, 'last_success_at': str(last_success),
                 'last_changed_at': str(last_changed)}
        if str(external_id) in persisted:
            counts = persisted[str(external_id)].get('ingest_counts', {})
            if counts.get('new_products') == 1:
                state = 'NEW'
                summary['NEW'] += 1
            else:
                summary['REDISCOVERED'] += 1
                if counts.get('changed_products') or counts.get('changed_offers'):
                    state = 'REDISCOVERED_UPDATED'
                    summary['REDISCOVERED_UPDATED'] += 1
                else:
                    state = 'REDISCOVERED_UNCHANGED'
                    summary['REDISCOVERED_UNCHANGED'] += 1
            entry['state'] = state
            entry['run1_price'] = persisted[str(external_id)].get('price')
            entry['run1_availability'] = persisted[str(external_id)].get('availability')
        else:
            state = 'HISTORICAL_NOT_REDISCOVERED'
            summary['HISTORICAL_NOT_REDISCOVERED'] += 1
            if active:
                summary['historical_active'] += 1
            else:
                summary['historical_deactivated'] += 1
            if last_changed is not None and last_changed.isoformat() > run1['started_at']:
                summary['historical_touched_after_run1_started'] += 1
                entry['state'] = 'HISTORICAL_TOUCHED_AFTER_RUN1_STARTED'
            else:
                entry['state'] = state
        ledger.append(entry)
    untouched_ok = (summary['historical_deactivated'] == 0
                    and summary['historical_touched_after_run1_started'] == 0
                    and all(entry['missed_crawls'] == 0 for entry in ledger
                            if entry['state'] == 'HISTORICAL_NOT_REDISCOVERED'))
    summary['status'] = 'RECONCILED_UNTOUCHED_VERIFIED' if untouched_ok else 'RECONCILED_DEFECT'
    (REPORTS / 'INTERVESP_OLD_VS_NEW.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    with (REPORTS / 'INTERVESP_OLD_VS_NEW_LEDGER.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ledger[0].keys()))
        writer.writeheader()
        writer.writerows(ledger)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if summary['status'] != 'RECONCILED_UNTOUCHED_VERIFIED':
        raise SystemExit(4)


if __name__ == '__main__':
    main()
