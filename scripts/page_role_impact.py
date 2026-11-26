"""Offline page-role replay of the frozen 2634. Does not rewrite journals or PostgreSQL."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o
from scripts.final_hotspot36 import S as HOT

H = r.BASE / 'PRE_FINAL_HOTSPOT_SWEEP_2026-10-16'

PHASE = r.BASE / 'PAGE_ROLE_SCOPE_2026-10-16'
OUTCOME = {
    'SUCCESS': 'PERSISTED',
    'AMBIGUOUS': 'AMBIGUOUS',
    'NON_PRODUCT_CATEGORY_PAGE': 'NON_PRODUCT_CATEGORY_PAGE',
    'NON_PRODUCT_EDITORIAL_PAGE': 'NON_PRODUCT_EDITORIAL_PAGE',
}


def key_of(row):
    entry = row.get('entry', row)
    return entry['supplier'], entry['index']


def impact():
    assert not (r.BASE / 'PRIVATE/pg17/postmaster.pid').exists(), 'PG_PID_PRESENT'
    plan = r.load(HOT / 'EFFECTIVE_ROW_PLAN.json')['rows']
    assert len(plan) == 2634
    protected = {}
    for label, cohort in r.load(H / 'HOTSPOT_PROTECTED_COHORTS.json')['cohorts'].items():
        protected[label] = [key_of(row) for row in cohort['rows']]
        assert len(protected[label]) == cohort['count']
    authorized36 = [key_of(row) for row in r.load(HOT / 'AUTHORIZED_36_ROWS.json')['rows']]
    assert len(authorized36) == 36
    counts = Counter()
    changed = []
    payload_delta = 0
    field_delta = Counter()
    current = {}
    with o.offline_guard() as network, patch('psycopg.connect', side_effect=AssertionError('PRE_DB_SQL_PROHIBITED')):
        for number, entry in enumerate(plan, 1):
            path = o.ROOT / entry['journal']
            assert r.digest(path.read_bytes()) == entry['journal_sha256']
            journal = r.load(path)
            if entry['outcome'] == 'NON_PRODUCT_CATEGORY_PAGE':
                assert o.extract(journal)[0] == entry['outcome']
                counts['category'] += 1
                current[entry['supplier'], entry['index']] = dict(outcome=entry['outcome'], persisted=False)
                continue
            result = journal['result']
            response = r.load(r.OUT / result['response_record'])
            raw = (r.OUT / response['raw_path']).read_bytes()
            body = (r.OUT / 'EVIDENCE' / entry['supplier'] / (result['evidence_sha256'] + '.html')).read_bytes()
            assert r.digest(raw) == result['response_sha256'] == response['response_sha256']
            assert r.digest(body) == result['evidence_sha256']
            assert o.sanitise_for_charset(raw, result['charset']) == body
            text = raw.decode(result['charset'] or 'utf8', 'replace')
            status, diagnostics = o._card_gate(entry['supplier'], text, result['final_url'])
            outcome = OUTCOME.get(status.value, status.value)
            if status.value == 'AMBIGUOUS' and 'MULTI_VARIANT' in diagnostics:
                outcome = 'MULTI_VARIANT'
            parsed = asdict(r.parse(entry['supplier'], body, result['final_url']))
            before = r.load(HOT / 'PARSED_ROWS' / entry['supplier'] / f"{entry['index']:06d}.json")['parsed_product']
            same = r.encoded(parsed) == r.encoded(before)
            if not same:
                payload_delta += 1
                for field in ('site_internal_id', 'name', 'availability'):
                    if parsed.get(field) != before.get(field):
                        field_delta[field] += 1
                if parsed.get('price') != before.get('price'):
                    field_delta['price'] += 1
            persisted = outcome == 'PERSISTED'
            counts['persisted' if persisted else 'terminal'] += 1
            counts[outcome] += 1
            row = dict(supplier=entry['supplier'], index=entry['index'], external_id=entry['external_id'],
                       url=entry['url'], previous_outcome=entry['outcome'], outcome=outcome,
                       outcome_changed=outcome != entry['outcome'], payload_unchanged=same,
                       diagnostics=list(diagnostics))
            current[entry['supplier'], entry['index']] = row
            if outcome != entry['outcome']:
                changed.append(row)
            if number % 250 == 0:
                print('PAGE ROLE IMPACT', number, '/2634', len(changed), 'outcome changes', flush=True)
        assert network['supplier_http_gets'] == 0 and network['prohibited_transport_attempts'] == 0
    changed_keys = {(row['supplier'], row['index']) for row in changed}
    editorial = {('ltt_msk', 169), ('ltt_msk', 170), ('ltt_msk', 171), ('ltt_msk', 172)}
    assert changed_keys == editorial
    assert all(row['outcome'] == 'NON_PRODUCT_EDITORIAL_PAGE' and row['previous_outcome'] == 'PERSISTED' for row in changed)
    assert payload_delta == 0 and not field_delta
    assert counts['PERSISTED'] == 2449
    assert counts['NON_PRODUCT_EDITORIAL_PAGE'] == 4
    assert counts['AMBIGUOUS'] == 114 and counts['MULTI_VARIANT'] == 66
    assert counts['category'] == 1 and counts['persisted'] == 2449 and counts['terminal'] == 184
    plan_by_key = {(row['supplier'], row['index']): row['outcome'] for row in plan}
    overlap = {}
    for label, keys in protected.items():
        overlap[label] = [list(key) for key in keys if key in editorial]
        for supplier, index in keys:
            row = current[supplier, index]
            if (supplier, index) in editorial:
                assert row['outcome'] == 'NON_PRODUCT_EDITORIAL_PAGE' and row['payload_unchanged']
                continue
            assert row['outcome'] == plan_by_key[supplier, index], ('PROTECTED_OUTCOME', label, supplier, index)
            if 'payload_unchanged' in row:
                assert row['payload_unchanged'] and not row['outcome_changed'], ('PROTECTED_DELTA', label, supplier, index)
    assert {tuple(item) for item in overlap['OPERATOR106']} == {('ltt_msk', 170), ('ltt_msk', 171), ('ltt_msk', 172)}
    assert all(not rows for label, rows in overlap.items() if label != 'OPERATOR106')
    for supplier, index in authorized36:
        row = current[supplier, index]
        assert row['outcome'] == 'PERSISTED' and row['payload_unchanged'] and not row['outcome_changed']
    for index in (941, 958):
        assert current['ltt_sib', index]['outcome'] == 'PERSISTED' and not current['ltt_sib', index]['outcome_changed']
    for external_id in ('312', '297'):
        match = next(row for row in plan if row['supplier'] == 'ltt_yug' and str(row['external_id']) == external_id)
        assert current['ltt_yug', match['index']]['outcome'] == 'PERSISTED'
        assert not current['ltt_yug', match['index']]['outcome_changed']
    assert current['ltt_yug', 591]['outcome'] == 'NON_PRODUCT_CATEGORY_PAGE'
    report = dict(
        status='PASS', scope=2634, inputs='2634/2634',
        newly_rejected_product_pages=4,
        unchanged_valid_persisted_pages=2449,
        terminal_outcome_changes=4,
        non_kind_payload_delta=0,
        identity_delta=0, title_delta=0, price_delta=0, currency_delta=0, availability_delta=0,
        counts=dict(counts),
        protected={label: len(keys) for label, keys in protected.items()},
        protected_outcome_overlap=overlap,
        operator106_kind_payload_unchanged=106,
        operator106_editorial_outcome_changes=3,
        authorized36_unchanged=36,
        yug_312_297_unchanged=True,
        yug_category_index_591_unchanged=True,
        sib_941_958_unchanged=True,
        changed_rows=changed,
        db_writes=0, supplier_http=0, sql_connections=0,
        network=network,
        checked_at=r.now(),
    )
    r.save(PHASE / 'PAGE_ROLE_FULL_IMPACT.json', report)
    print('PAGE ROLE IMPACT PASS', counts, flush=True)


if __name__ == '__main__':
    impact()
