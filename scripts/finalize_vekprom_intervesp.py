"""Final artifact assembly for the vekprom/intervesp source integration.

Verifies cross-namespace isolation (no other supplier gained rows, captures or
observations after RUN1 started), records DB fingerprints, runs the adapter and
engine pytest files for TEST_RESULTS.json, and renders REPORT.md from the
collected artifacts.  Read-only with respect to supplier data.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
BASE_NAMESPACES = {'kami': 5264, 'beka_mak': 109, 'intervesp': 1642, 'beka_mak_tr': 78,
                   'optimum': 1353, 'partner_st': 1225}
TEST_FILES = ['tests/test_vekprom_adapter.py', 'tests/test_full_supplier_collection.py',
              'tests/test_commercial_persistence.py', 'tests/test_qa_availability_regression.py']


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(name: str) -> dict:
    return json.loads((REPORTS / name).read_text(encoding='utf-8'))


def render_report_md(report: dict) -> str:
    lines = ['# vekprom + intervesp source integration report', '',
             f"Generated: {report['generated_at']}  ",
             f"RUN1 window started: {report['run1_window_started']}", '',
             '## Gates', '', '| gate | status |', '| --- | --- |']
    lines.extend(f'| {gate} | {status} |' for gate, status in report['gates'].items())
    lines += ['', '## RUN1 outcome counters', '', '| supplier | status counters |', '| --- | --- |']
    lines.extend(f"| {code} | `{json.dumps(counts, ensure_ascii=False)}` |"
                 for code, counts in report['run1_counts'].items())
    lines += ['', '## Isolation', '', f"Status: `{report['isolation']['status']}`", '',
              '| namespace | source_products | catalog-linked |', '| --- | --- | --- |']
    lines.extend(f"| {code} | {count} | {report['isolation']['catalog_linked'].get(code, 0)} |"
                 for code, count in sorted(report['isolation']['namespaces'].items()))
    iso = report['isolation']
    lines += ['', f"source_products total: {iso['source_products_total']}",
              f"foreign rows/observations/captures since RUN1 start: "
              f"`{iso['foreign_new_rows_since_run1']}` / `{iso['foreign_observations_since_run1']}` / "
              f"`{iso['foreign_captures_since_run1']}`", '',
              '## Intervesp historical reconciliation', '',
              f"`{json.dumps(report['reconciliation'], ensure_ascii=False)}`", '',
              '## Tests', '', '| file | summary |', '| --- | --- |']
    lines.extend(f'| {name} | {summary} |' for name, summary in report['tests'].items())
    lines += ['', '## Fingerprints', '']
    lines.append(f"- table counts: `{report['fingerprints']['table_counts']}`")
    lines.append(f"- vekprom offers: `{report['fingerprints']['vekprom_offers']}`")
    lines.append(f"- vekprom price states: `{report['fingerprints']['vekprom_price_states']}`")
    lines.append(f"- vekprom top categories: `{report['fingerprints']['vekprom_top_categories']}`")
    lines.append('')
    return '\n'.join(lines)


def main() -> None:
    vek_run1 = load('VEKPROM_RUN1/CHECKPOINT.json')
    iv_run1 = load('INTERVESP_RUN1/CHECKPOINT.json')
    vek_run2 = load('VEKPROM_RUN2/RUN2_CHECKPOINT.json')
    iv_run2 = load('INTERVESP_RUN2/RUN2_CHECKPOINT.json')
    vek_qa = load('QA_GATE_VEKPROM.json')
    iv_qa = load('QA_GATE_INTERVESP.json')
    reconciliation = load('INTERVESP_OLD_VS_NEW.json')
    run1_start = min(vek_run1['started_at'], iv_run1['started_at'])
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    repository = PostgresRepository(PostgresConfig.from_env())
    report: dict = {'generated_at': now_iso(), 'run1_window_started': run1_start}
    try:
        with repository.connection.cursor() as cursor:
            namespaces = dict(cursor.execute('SELECT s.code,count(p.id) FROM suppliers s '
                                             'LEFT JOIN source_products p ON p.supplier_id=s.id '
                                             'GROUP BY s.code').fetchall())
            linked = dict(cursor.execute('SELECT s.code,count(*) FILTER (WHERE p.catalog_product_id IS NOT NULL) '
                                         'FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id '
                                         'GROUP BY s.code').fetchall())
            totals = cursor.execute('SELECT count(*) FROM source_products').fetchone()[0]
            foreign_new = cursor.execute("""SELECT s.code,count(*) FROM source_products p
                    JOIN suppliers s ON s.id=p.supplier_id WHERE s.code NOT IN ('vekprom','intervesp')
                    AND p.first_seen_at > %s::timestamptz GROUP BY s.code""",
                                         (run1_start,)).fetchall()
            foreign_obs = cursor.execute("""SELECT s.code,count(*) FROM offer_commercial_observations o
                    JOIN suppliers s ON s.id=o.supplier_id WHERE s.code NOT IN ('vekprom','intervesp')
                    AND o.observed_at > %s::timestamptz GROUP BY s.code""", (run1_start,)).fetchall()
            foreign_cap = cursor.execute("""SELECT s.code,count(*) FROM supplier_http_captures c
                    JOIN suppliers s ON s.id=c.supplier_id WHERE s.code NOT IN ('vekprom','intervesp')
                    AND c.observed_at > %s::timestamptz GROUP BY s.code""", (run1_start,)).fetchall()
            table_counts = {table: cursor.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                            for table in ('suppliers', 'source_products', 'offers', 'product_matches',
                                          'catalog_products', 'catalog_offer_selection',
                                          'supplier_http_captures', 'offer_commercial_observations',
                                          'schema_migrations')}
            vekprom_detail = cursor.execute("""SELECT count(*),count(*) FILTER (WHERE o.price IS NOT NULL),
                    count(*) FILTER (WHERE o.availability_normalized='in_stock') FROM offers o
                    JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='vekprom'""").fetchone()
            vekprom_states = dict(cursor.execute("""SELECT obs.price_state,count(*) FROM offer_commercial_observations obs
                    JOIN suppliers s ON s.id=obs.supplier_id WHERE s.code='vekprom' GROUP BY 1""").fetchall())
            vekprom_categories = dict(cursor.execute("""SELECT p.raw_data->>'source_category',count(*)
                    FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                    WHERE s.code='vekprom' GROUP BY 1 ORDER BY 2 DESC LIMIT 10""").fetchall())
    finally:
        repository.close()
    isolation_ok = (not foreign_new and not foreign_obs and not foreign_cap
                    and all(namespaces.get(code) == count for code, count in BASE_NAMESPACES.items()
                            if code != 'intervesp')
                    and linked.get('partner_st') == 117 and linked.get('intervesp') == 0
                    and linked.get('vekprom', 0) == 0)
    report['isolation'] = {'namespaces': namespaces, 'catalog_linked': linked,
                           'source_products_total': totals,
                           'foreign_new_rows_since_run1': dict(foreign_new),
                           'foreign_observations_since_run1': dict(foreign_obs),
                           'foreign_captures_since_run1': dict(foreign_cap),
                           'status': 'ISOLATION_VERIFIED' if isolation_ok else 'ISOLATION_DEFECT'}
    report['fingerprints'] = {'table_counts': table_counts,
                              'vekprom_offers': {'total': vekprom_detail[0], 'numeric_price': vekprom_detail[1],
                                                 'in_stock': vekprom_detail[2]},
                              'vekprom_price_states': vekprom_states,
                              'vekprom_top_categories': vekprom_categories}
    test_results = {'recorded_at': now_iso(), 'files': {}, 'passed': True}
    for test_file in TEST_FILES:
        proc = subprocess.run([sys.executable, '-m', 'pytest', test_file, '-q', '--no-header'],
                              cwd=ROOT, capture_output=True, text=True)
        test_results['files'][test_file] = {
            'exit_code': proc.returncode,
            'summary_tail': proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else '',
            'stdout_tail': proc.stdout[-2000:], 'stderr_tail': proc.stderr[-2000:]}
        test_results['passed'] = test_results['passed'] and proc.returncode == 0
    (REPORTS / 'TEST_RESULTS.json').write_text(json.dumps(test_results, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
    report['tests'] = {name: value['summary_tail'] for name, value in test_results['files'].items()}
    ok = (isolation_ok and test_results['passed']
          and vek_run1.get('status') == 'COMPLETED' and iv_run1.get('status') == 'COMPLETED'
          and vek_run2.get('status') == 'RUN2_EXACT_NOOP_VERIFIED'
          and iv_run2.get('status') == 'RUN2_EXACT_NOOP_VERIFIED'
          and vek_qa.get('status') == 'QA_GATE_PASSED' and iv_qa.get('status') == 'QA_GATE_PASSED'
          and reconciliation.get('status') == 'RECONCILED_UNTOUCHED_VERIFIED')
    report['gates'] = {'vekprom_run1': vek_run1.get('status'), 'intervesp_run1': iv_run1.get('status'),
                       'vekprom_run2': vek_run2.get('status'), 'intervesp_run2': iv_run2.get('status'),
                       'vekprom_qa': vek_qa.get('status'), 'intervesp_qa': iv_qa.get('status'),
                       'intervesp_reconciliation': reconciliation.get('status')}
    report['run1_counts'] = {
        'vekprom': dict(Counter(str(r.get('status')) for r in vek_run1['applied'].values())),
        'intervesp': dict(Counter(str(r.get('status')) for r in iv_run1['applied'].values()))}
    report['reconciliation'] = {key: value for key, value in reconciliation.items()
                                if key != 'rows' or isinstance(value, (int, str))}
    (REPORTS / 'FINAL_STATUS.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                               encoding='utf-8')
    (REPORTS / 'REPORT.md').write_text(render_report_md(report), encoding='utf-8')
    print(json.dumps({'isolation': report['isolation']['status'], 'gates': report['gates'],
                      'overall': 'ALL_GATES_PASSED' if ok else 'GATE_FAILURE'}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if ok else 4)


if __name__ == '__main__':
    main()
