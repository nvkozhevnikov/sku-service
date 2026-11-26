"""Audit the restored intervesp namespace before the authorized live refresh.

Read-only: external_id shape, source_url forms, raw_data provenance keys,
offer price/availability distribution and the listing/detail identity mapping
inputs. Writes INTERVESP_BASELINE_AUDIT.json; prints a compact summary.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'


def main() -> None:
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    repository = PostgresRepository(PostgresConfig.from_env())
    audit: dict = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'supplier': 'intervesp'}
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute("""SELECT p.external_id,p.sku,p.source_url,p.canonical_url,p.name,
                   p.raw_data ? 'listing_identity' AS has_listing,
                   p.raw_data ? 'commercial_capture' AS has_capture,
                   p.raw_data ? '_commercial_projection_region' AS has_region,
                   p.last_success_at,o.price,o.currency,o.availability_normalized,o.active
                   FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                   LEFT JOIN offers o ON o.source_product_id=p.id AND o.offer_kind='default'
                   WHERE s.code='intervesp' ORDER BY p.external_id""")
            rows = cursor.fetchall()
            cursor.execute("""SELECT count(*),count(*) FILTER (WHERE active),
                   count(*) FILTER (WHERE missed_crawls>0),min(external_id),max(external_id),
                   count(*) FILTER (WHERE external_id LIKE 'url-sha256:%'),
                   count(*) FILTER (WHERE external_id !~ '^url-sha256:')
                   FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='intervesp'""")
            totals = cursor.fetchone()
            cursor.execute("""SELECT o.availability_normalized,count(*) FROM offers o
                   JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='intervesp'
                   AND o.offer_kind='default' GROUP BY 1""")
            availability = dict(cursor.fetchall())
            cursor.execute("""SELECT count(*) FROM offer_commercial_observations o
                   JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='intervesp'""")
            observations = cursor.fetchone()[0]
    finally:
        repository.close()
    path_shapes = Counter()
    listing_keys = Counter()
    price_states = Counter()
    samples = []
    for (external_id, sku, source_url, canonical_url, name, has_listing, has_capture,
         has_region, last_success, price, currency, availability_raw, offer_active) in rows:
        path_shapes['/'.join(segment for segment in source_url.split('/')[3:5])] = 1 if False else path_shapes.get(
            '/'.join(segment for segment in source_url.split('/')[3:5]), 0) + 1
        listing_keys['listing' if has_listing else 'no_listing'] += 1
        listing_keys['capture' if has_capture else 'no_capture'] += 1
        listing_keys['region' if has_region else 'no_region'] += 1
        price_states['numeric' if price is not None else 'null_price'] += 1
        if len(samples) < 5:
            samples.append({'external_id': external_id, 'sku': sku, 'source_url': source_url,
                            'name': (name or '')[:80], 'price': str(price), 'currency': currency,
                            'availability_normalized': availability_raw,
                            'last_success_at': str(last_success), 'offer_active': offer_active})
    audit.update({
        'rows': totals[0], 'active_rows': totals[1], 'rows_with_missed_crawls': totals[2],
        'external_id_min': str(totals[3]), 'external_id_max': str(totals[4]),
        'external_id_url_sha256': totals[5], 'external_id_other': totals[6],
        'distinct_external_ids': len({row[0] for row in rows}),
        'source_url_prefixes': dict(listing_keys), 'path_shapes': dict(path_shapes),
        'offer_availability_distribution': availability,
        'offer_price_distribution': dict(price_states),
        'observations_history': observations, 'samples': samples})
    audit['status'] = 'AUDIT_RECORDED'
    (REPORTS / 'INTERVESP_BASELINE_AUDIT.json').write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: audit[k] for k in ('status', 'rows', 'active_rows', 'distinct_external_ids',
                                            'external_id_min', 'external_id_max',
                                            'offer_availability_distribution', 'observations_history')},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
