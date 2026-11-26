"""Authorized eligibility revocation for four MSK editorial source products.

Isolated PG55455 only. Sets source_products 225-228 active=false. Does not
touch offers, captures, observations, missed_crawls, or historical RUN1 journals.
"""
from collections import Counter
from dataclasses import asdict
import copy
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import page_role_seals as seals
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o
from scripts.export_ltt_uss_readonly_snapshot import (
    BIN, DATA, EXPECTED_SYSTEM_ID, port_listening, shutdown_receipt, stop_postgres)
from scripts.verify_inverse97_resume import guarded_repository, same
import psycopg

PHASE = r.BASE / 'PAGE_ROLE_REMEDIATION_2026-10-16'
HOT = r.BASE / 'FINAL_HOTSPOT36_BATCH_2026-10-16'
SCOPE = r.BASE / 'PAGE_ROLE_SCOPE_2026-10-16'
PLAN = HOT / 'EFFECTIVE_ROW_PLAN.json'
BASELINE = HOT / 'HOTSPOT36_REVISED_BASELINE.json'
FOUR = (
    dict(source_product_id=225, offer_id=225, capture_id=250, observation_id=250,
         external_id='1717', index=169),
    dict(source_product_id=226, offer_id=226, capture_id=251, observation_id=251,
         external_id='1721', index=170),
    dict(source_product_id=227, offer_id=227, capture_id=252, observation_id=252,
         external_id='1723', index=171),
    dict(source_product_id=228, offer_id=228, capture_id=253, observation_id=253,
         external_id='1719', index=172),
)
IDS = tuple(row['source_product_id'] for row in FOUR)
OUTCOME = {
    'SUCCESS': 'PERSISTED',
    'AMBIGUOUS': 'AMBIGUOUS',
    'NON_PRODUCT_CATEGORY_PAGE': 'NON_PRODUCT_CATEGORY_PAGE',
    'NON_PRODUCT_EDITORIAL_PAGE': 'NON_PRODUCT_EDITORIAL_PAGE',
}


def history():
    seals.verify(baseline_seal=seals.ACCEPTED, proposed_transition=seals.PROPOSED)
    impact = r.load(SCOPE / 'PAGE_ROLE_FULL_IMPACT.json')
    assert impact['status'] == 'PASS' and impact['newly_rejected_product_pages'] == 4
    assert {(row['supplier'], row['index']) for row in impact['changed_rows']} == {('ltt_msk', row['index']) for row in FOUR}
    plan = r.load(PLAN)['rows']
    assert len(plan) == 2634
    for entry in plan:
        assert r.digest((o.ROOT / entry['journal']).read_bytes()) == entry['journal_sha256']
    return plan


def gate_outcome(supplier, text, url):
    status, diagnostics = o._card_gate(supplier, text, url)
    outcome = OUTCOME.get(status.value, status.value)
    if status.value == 'AMBIGUOUS' and 'MULTI_VARIANT' in diagnostics:
        outcome = 'MULTI_VARIANT'
    return outcome, status, diagnostics


def frozen_text(journal):
    result = journal['result']
    response = r.load(o.safe_path(r.OUT, result['response_record']))
    raw = o.safe_path(r.OUT, response['raw_path']).read_bytes()
    assert r.digest(raw) == response['response_sha256'] == result['response_sha256']
    return raw.decode(result['charset'] or 'utf8', 'replace'), result


def replay_roles(plan):
    """Current gate versus the accepted hotspot36 plan. Only the four articles may move."""
    allowed = {('ltt_msk', row['index']) for row in FOUR}
    counts = Counter()
    changed = []
    with o.offline_guard() as network:
        for number, entry in enumerate(plan, 1):
            journal = r.load(o.ROOT / entry['journal'])
            if entry['outcome'] == 'NON_PRODUCT_CATEGORY_PAGE':
                assert o.extract(journal)[0] == entry['outcome']
                counts['NON_PRODUCT_CATEGORY_PAGE'] += 1
                continue
            text, result = frozen_text(journal)
            outcome, status, diagnostics = gate_outcome(entry['supplier'], text, result['final_url'])
            counts[outcome] += 1
            if outcome != entry['outcome']:
                changed.append((entry['supplier'], entry['index'], entry['outcome'], outcome, list(diagnostics)))
            if number % 400 == 0:
                print('ROLE REPLAY', number, '/2634', flush=True)
        assert network['supplier_http_gets'] == 0 and network['prohibited_transport_attempts'] == 0
    assert {(row[0], row[1]) for row in changed} == allowed, changed
    assert all(row[2] == 'PERSISTED' and row[3] == 'NON_PRODUCT_EDITORIAL_PAGE' for row in changed)
    assert counts['PERSISTED'] == 2449 and counts['NON_PRODUCT_EDITORIAL_PAGE'] == 4
    assert counts['AMBIGUOUS'] == 114 and counts['MULTI_VARIANT'] == 66
    assert counts['NON_PRODUCT_CATEGORY_PAGE'] == 1
    return dict(counts)


def build_journal(historical, historical_path, status, diagnostics):
    fresh = copy.deepcopy(historical)
    fresh['outcome'] = 'NON_PRODUCT_EDITORIAL_PAGE'
    fresh['result']['status'] = status.value
    fresh['result']['diagnostics'] = list(diagnostics)
    fresh['journal_generation'] = dict(
        generation='PAGE_ROLE_JOURNAL_GENERATION_2026-10-16',
        historical_journal=str(historical_path.relative_to(o.ROOT)),
        historical_journal_sha256=r.digest(historical_path.read_bytes()),
        historical_outcome='PERSISTED',
        historical_db_verification=fresh.pop('db_verification'),
        gate='PAGE_ROLE_GATE_CODE_SEAL',
        source_product_created=False,
        offer_created=False,
        evaluated_at=r.now(),
    )
    return fresh


def evaluate_four(plan):
    by_index = {entry['index']: entry for entry in plan if entry['supplier'] == 'ltt_msk'}
    built = []
    with o.offline_guard():
        for spec in FOUR:
            entry = by_index[spec['index']]
            assert entry['external_id'] == spec['external_id'] and entry['outcome'] == 'PERSISTED'
            path = o.ROOT / entry['journal']
            historical = r.load(path)
            assert historical['outcome'] == 'PERSISTED'
            assert historical['db_verification']['source_product_id'] == spec['source_product_id']
            assert historical['db_verification']['offer_id'] == spec['offer_id']
            assert historical['db_verification']['capture_id'] == spec['capture_id']
            assert historical['db_verification']['observation_id'] == spec['observation_id']
            text, result = frozen_text(historical)
            outcome, status, diagnostics = gate_outcome('ltt_msk', text, result['final_url'])
            assert outcome == 'NON_PRODUCT_EDITORIAL_PAGE', (spec['external_id'], outcome, diagnostics)
            fresh = build_journal(historical, path, status, diagnostics)
            assert o.extract(fresh) == ('NON_PRODUCT_EDITORIAL_PAGE', None, None)
            assert r.digest(path.read_bytes()) == entry['journal_sha256']
            built.append(dict(spec=spec, entry=entry, journal=fresh, diagnostics=list(diagnostics)))
    return built


def tests():
    selection = r.load(HOT / 'TEST_SELECTION.json')['files'] + ['tests/test_ltt_modx_page_role.py']
    xml = PHASE / 'TESTS.xml'
    PHASE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '--tb=line', f'--junitxml={xml}', *selection],
        cwd=o.ROOT, text=True, encoding='utf-8', errors='replace')
    import xml.etree.ElementTree as ET
    root = ET.parse(xml).getroot()
    cases = root.findall('.//testcase')
    receipt = dict(
        total=len(cases),
        passed=sum(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases),
        fail=len(root.findall('.//failure')),
        error=len(root.findall('.//error')),
        skip=len(root.findall('.//skipped')),
        exit_code=proc.returncode,
        xml_sha256=r.digest(xml.read_bytes()),
        command='python -m pytest -q --tb=line relevant selection + tests/test_ltt_modx_page_role.py',
    )
    r.save(PHASE / 'TEST_RECEIPT.json', receipt)
    assert proc.returncode == 0 and receipt['fail'] == receipt['error'] == receipt['skip'] == 0
    assert receipt['passed'] == receipt['total'] >= 4022
    return receipt


def preflight():
    assert not (DATA / 'postmaster.pid').exists(), 'PG_PID_PRESENT'
    assert not port_listening(), 'PORT_55455_LISTENING'
    assert not (PHASE / 'BEFORE_CORRECTION.json').exists(), 'Inspect durable state; never repeat correction'
    plan = history()
    roles = replay_roles(plan)
    built = evaluate_four(plan)
    receipt = tests()
    r.save(PHASE / 'OFFLINE_PREPARATION.json', dict(
        status='PASS', roles=roles, articles=[row['spec']['external_id'] for row in built],
        tests=receipt, database_started=False, supplier_http=0, checked_at=r.now()))
    print('PAGE ROLE PREFLIGHT PASS', roles, 'tests', receipt['passed'], flush=True)


def start_db():
    assert not (DATA / 'postmaster.pid').exists(), 'PG_PID_PRESENT'
    assert not port_listening(), 'PORT_55455_LISTENING'
    assert (BIN / 'pg_ctl.exe').is_file(), 'PG_BIN_MISSING'
    PHASE.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(BIN / 'pg_ctl.exe'), 'start', '-D', str(DATA), '-l', str(PHASE / 'postgres.log'),
         '-o', '-h 127.0.0.1 -p 55455', '-w', '-t', '30'],
        check=True, timeout=90)


def stop_db():
    stopped = stop_postgres()
    receipt = shutdown_receipt(stopped)
    r.save(PHASE / 'SHUTDOWN.json', receipt)
    assert receipt['SAFE_TO_POWER_OFF'] == 'YES', receipt
    return receipt


def connect():
    _, cfg = r.config()
    assert (cfg['host'], cfg['port'], cfg['dbname']) == ('127.0.0.1', 55455, 'universal_supplier_ltt_uss')
    connection = psycopg.connect(**cfg, autocommit=True)
    connection.execute("SET application_name='ltt_uss_page_role_remediation'")
    connection.execute("SET statement_timeout='120s'")
    connection.execute("SET lock_timeout='5s'")
    return connection


def projection_delta(before, after):
    before_pairs = {pair[0]['id']: pair for pair in before['product_offer_projections']}
    after_pairs = {pair[0]['id']: pair for pair in after['product_offer_projections']}
    assert set(before_pairs) == set(after_pairs)
    changed = []
    for product_id, pair in before_pairs.items():
        if r.encoded(pair) != r.encoded(after_pairs[product_id]):
            changed.append(product_id)
    return changed


def assert_only_active(before, after):
    changed = projection_delta(before, after)
    assert sorted(changed) == sorted(IDS), changed
    before_pairs = {pair[0]['id']: pair for pair in before['product_offer_projections']}
    after_pairs = {pair[0]['id']: pair for pair in after['product_offer_projections']}
    for product_id in IDS:
        product, offer = before_pairs[product_id]
        after_product, after_offer = after_pairs[product_id]
        assert r.encoded(offer) == r.encoded(after_offer)
        expected = copy.deepcopy(product)
        assert expected['active'] is True and expected['missed_crawls'] == 0
        expected['active'] = False
        assert r.encoded(expected) == r.encoded(after_product)
    changed_tables = sorted(
        name for name, row in before['fingerprints'].items() if row != after['fingerprints'][name])
    assert changed_tables == ['source_products'], changed_tables
    assert r.encoded(before['sequences']) == r.encoded(after['sequences'])
    assert r.encoded(before['special_products']) == r.encoded(after['special_products'])
    assert r.encoded(before['sib_units']) == r.encoded(after['sib_units'])
    assert before['totals'] == after['totals'] == dict(
        source_products=2453, offers=2453, supplier_http_captures=2533, offer_commercial_observations=2533)
    for supplier, tables in after['supplier_namespaces'].items():
        for name, digest in tables.items():
            if supplier == 'ltt_msk' and name == 'source_products':
                assert digest != before['supplier_namespaces'][supplier][name]
            else:
                assert digest == before['supplier_namespaces'][supplier][name], (supplier, name)


def read_targets(connection):
    found = connection.execute(
        '''SELECT row_to_json(p), row_to_json(o), s.code
           FROM source_products p
           JOIN suppliers s ON s.id=p.supplier_id
           JOIN offers o ON o.source_product_id=p.id
           WHERE p.id = ANY(%s)
           ORDER BY p.id''', (list(IDS),)).fetchall()
    assert len(found) == 4
    return found


def require_prior_state(rows):
    expected = {spec['source_product_id']: spec for spec in FOUR}
    for product, offer, supplier in rows:
        spec = expected[product['id']]
        if supplier != 'ltt_msk' or product['external_id'] != spec['external_id']:
            raise AssertionError('TARGET_IDENTITY_MISMATCH')
        if product['active'] is not True or product['missed_crawls'] != 0 or product['catalog_product_id'] is not None:
            raise AssertionError('TARGET_PRODUCT_STATE_MISMATCH')
        if offer['id'] != spec['offer_id'] or offer['active'] is not False or offer['missed_crawls'] != 0:
            raise AssertionError('OFFER_NOT_ALREADY_INACTIVE')
        if offer['source_product_id'] != product['id']:
            raise AssertionError('OFFER_LINK_MISMATCH')


def cas_deactivate(connection, before):
    after = copy.deepcopy(before)
    after['active'] = False
    got = connection.execute(
        '''UPDATE source_products p SET active=false
           WHERE id=%s AND active=true AND missed_crawls=0 AND catalog_product_id IS NULL
             AND row_to_json(p)::jsonb=%s::jsonb
           RETURNING row_to_json(p)''',
        (before['id'], r.encoded(before).decode('utf8'))).fetchone()
    assert got is not None and r.encoded(got[0]) == r.encoded(after), 'COMPARE_AND_SET_FAILED'
    return got[0]


def apply(connection):
    history()
    gate = r.load(PHASE / 'OFFLINE_PREPARATION.json')
    assert gate['status'] == 'PASS' and gate['database_started'] is False
    system = r.verify_system()
    assert system['system_identifier'] == EXPECTED_SYSTEM_ID and system['version'] == '17.11'
    before = o.snapshot(connection)
    same(before, r.load(BASELINE))
    assert len(before['fingerprints']) == 51 and len(before['sequences']) == 40
    rows = read_targets(connection)
    try:
        require_prior_state(rows)
    except AssertionError as error:
        r.save(PHASE / 'BLOCKER.json', dict(stage='PRE_WRITE', error=str(error), checked_at=r.now()))
        raise
    captures = connection.execute(
        'SELECT id FROM supplier_http_captures WHERE id = ANY(%s) ORDER BY id',
        ( [spec['capture_id'] for spec in FOUR],)).fetchall()
    observations = connection.execute(
        'SELECT id FROM offer_commercial_observations WHERE id = ANY(%s) ORDER BY id',
        ([spec['observation_id'] for spec in FOUR],)).fetchall()
    assert [row[0] for row in captures] == [250, 251, 252, 253]
    assert [row[0] for row in observations] == [250, 251, 252, 253]
    r.save(PHASE / 'BEFORE_CORRECTION.json', dict(**before, **system, checked_at=r.now()))
    o.clients(connection)
    rollback_path = PHASE / 'ROLLBACK_BEFORE_COMMIT.json'
    with connection.transaction():
        locked = connection.execute(
            '''SELECT row_to_json(p), row_to_json(o), s.code
               FROM source_products p
               JOIN suppliers s ON s.id=p.supplier_id
               JOIN offers o ON o.source_product_id=p.id AND o.id = ANY(%s)
               WHERE p.id = ANY(%s)
               ORDER BY p.id
               FOR UPDATE OF p, o''',
            ([spec['offer_id'] for spec in FOUR], list(IDS))).fetchall()
        assert len(locked) == 4
        require_prior_state(locked)
        assert not rollback_path.exists(), 'ROLLBACK_EVIDENCE_ALREADY_EXISTS_INSPECT_DURABLE_STATE'
        r.save(rollback_path, dict(
            operation='ELIGIBILITY_REVOCATION_RETAIN_HISTORY', target=('universal_supplier_ltt_uss', '127.0.0.1', 55455, '17.11'),
            system=system, rows=[dict(product=product, offer=offer) for product, offer, _supplier in locked],
            captures_deleted=0, observations_deleted=0, missed_crawls_changed=0,
            checked_at=r.now(), commit_pending=True))
        updated = [cas_deactivate(connection, product) for product, _offer, _supplier in locked]
        assert [row['id'] for row in updated] == list(IDS)
        confirmed = read_targets(connection)
        for product, offer, supplier in confirmed:
            assert product['active'] is False and product['missed_crawls'] == 0 and supplier == 'ltt_msk'
            assert offer['active'] is False
    second = verify_only(connection)
    assert second['changed_rows'] == 0
    after = o.snapshot(connection)
    assert_only_active(before, after)
    active = connection.execute('SELECT count(*) FROM source_products WHERE active').fetchone()[0]
    before_active = sum(1 for pair in before['product_offer_projections'] if pair[0]['active'])
    assert before_active - active == 4
    receipt = dict(
        operation='ELIGIBILITY_REVOCATION_RETAIN_HISTORY', committed=True, changed_rows=4,
        source_products_changed=4, offers_changed=0, captures_deleted=0, observations_deleted=0,
        missed_crawls_changed=0, idempotent_second_execution_changed_rows=0,
        active_before=before_active, active_after=active, active_delta=-4,
        rollback_sha256=r.digest(rollback_path.read_bytes()), system=system, checked_at=r.now())
    r.save(PHASE / 'REMEDIATION_RECEIPT.json', receipt)
    r.save(PHASE / 'IDEMPOTENCY_RECEIPT.json', second)
    r.save(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json', dict(
        **after, **system, status='PAGE_ROLE_REMEDIATED_REVISED_BASELINE', checked_at=r.now()))
    print('PAGE ROLE DEACTIVATED 4 / offers 0 / idempotency 0', flush=True)
    return receipt


def verify_only(connection):
    locked = connection.execute(
        '''SELECT row_to_json(p), row_to_json(o), s.code
           FROM source_products p
           JOIN suppliers s ON s.id=p.supplier_id
           JOIN offers o ON o.source_product_id=p.id
           WHERE p.id = ANY(%s)
           ORDER BY p.id
           FOR UPDATE OF p, o''', (list(IDS),)).fetchall()
    assert len(locked) == 4
    for product, offer, supplier in locked:
        assert supplier == 'ltt_msk' and product['active'] is False and product['missed_crawls'] == 0
        assert offer['active'] is False and offer['missed_crawls'] == 0
    return dict(operation='ELIGIBILITY_REVOCATION_RETAIN_HISTORY', changed_rows=0, idempotent=True, verified_products=4,
                writes=0)


def write_journals(plan):
    assert r.load(PHASE / 'REMEDIATION_RECEIPT.json')['committed'] is True
    assert not (PHASE / 'JOURNALS').exists(), 'Journal generation already exists'
    built = evaluate_four(plan)
    rows = []
    for item in built:
        entry = item['entry']
        destination = PHASE / 'JOURNALS' / 'ltt_msk' / Path(entry['journal']).name
        r.save(destination, item['journal'])
        assert o.extract(r.load(destination)) == ('NON_PRODUCT_EDITORIAL_PAGE', None, None)
        assert r.digest((o.ROOT / entry['journal']).read_bytes()) == entry['journal_sha256']
        rows.append(dict(
            supplier='ltt_msk', index=entry['index'], external_id=entry['external_id'], url=entry['url'],
            outcome='NON_PRODUCT_EDITORIAL_PAGE', journal=str(destination.relative_to(o.ROOT)),
            journal_sha256=r.digest(destination.read_bytes()),
            historical_journal=entry['journal'], historical_journal_sha256=entry['journal_sha256'],
            diagnostics=item['diagnostics'], source_product_created=False, offer_created=False))
    fresh_plan = []
    replacements = {row['index']: row for row in rows}
    for entry in plan:
        if entry['supplier'] == 'ltt_msk' and entry['index'] in replacements:
            fresh_plan.append(replacements[entry['index']])
        else:
            fresh_plan.append(entry)
    assert len(fresh_plan) == 2634
    assert Counter(entry['outcome'] for entry in fresh_plan)['NON_PRODUCT_EDITORIAL_PAGE'] == 4
    assert Counter(entry['outcome'] for entry in fresh_plan)['PERSISTED'] == 2449
    r.save(PHASE / 'JOURNAL_GENERATION.json', dict(
        status='PASS', count=4, rows=rows, historical_journals_rewritten=0, checked_at=r.now()))
    r.save(PHASE / 'EFFECTIVE_ROW_PLAN.json', dict(rows=fresh_plan, source='hotspot36 plan plus four new journals'))
    print('JOURNAL GENERATION 4/4 NON_PRODUCT_EDITORIAL_PAGE', flush=True)
    return fresh_plan


def run2(connection):
    plan = r.load(PHASE / 'EFFECTIVE_ROW_PLAN.json')['rows']
    assert len(plan) == 2634
    baseline = r.load(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json')
    pre = r.load(PHASE / 'BEFORE_CORRECTION.json')
    checked = noops = 0
    counts = {supplier: Counter() for supplier in r.SOURCES}
    last = None
    out = PHASE / 'RUN2'
    with o.offline_guard() as network:
        system = r.verify_system()
        repo = guarded_repository(connection)
        before = o.snapshot(connection)
        same(before, baseline)
        assert len(before['fingerprints']) == 51 and len(before['sequences']) == 40
        o.clients(connection)
        r.save(out / 'BEFORE.json', dict(**before, **system, checked_at=r.now()))
        try:
            for entry in plan:
                last = entry
                path = o.ROOT / entry['journal']
                assert r.digest(path.read_bytes()) == entry['journal_sha256']
                journal = r.load(path)
                outcome, product, capture = o.extract(journal)
                assert outcome == entry['outcome']
                if product is not None:
                    r.verify_persisted(repo, product, capture)
                    ids = r.check_observation(repo, product, capture)
                    assert ids == journal['db_verification']
                    result = repo.persist_commercial_observation(product, capture)
                    assert result['exact_noop'] and not result['observation_created'] and not result['projection_updated']
                    assert all(value == 0 for value in asdict(result['ingest_counts']).values())
                    noops += 1
                counts[entry['supplier']][outcome] += 1
                checked += 1
                if checked % 200 == 0:
                    print('PAGE ROLE RUN2', checked, '/2634 noops', noops, flush=True)
            assert checked == 2634 and noops == 2449
            assert sum(counter['NON_PRODUCT_EDITORIAL_PAGE'] for counter in counts.values()) == 4
            after = o.snapshot(connection)
            same(after, baseline)
            inactive = connection.execute(
                'SELECT id, active, missed_crawls FROM source_products WHERE id = ANY(%s) ORDER BY id',
                (list(IDS),)).fetchall()
            assert inactive == [(225, False, 0), (226, False, 0), (227, False, 0), (228, False, 0)]
            # The only delta versus the pre-remediation snapshot is the four active flags.
            assert_only_active(pre, after)
            other = Counter()
            for counter in counts.values():
                for name, value in counter.items():
                    if name not in {'PERSISTED', 'NON_PRODUCT_EDITORIAL_PAGE'}:
                        other[name] += value
            result = dict(
                status='PASS', scope=checked, exact_noop_persisted=noops,
                non_product_editorial=4, other_terminal_counts=dict(other), sources=counts,
                table_fingerprint_delta=0, sequence_delta=0, new_source_products=0, new_offers=0,
                new_captures=0, new_observations=0, sql_writes=0, supplier_http=0,
                clients=o.clients(connection), network=network, checked_at=r.now())
            r.save(out / 'RESULT.json', result)
            print('PAGE ROLE RUN2 PASS', noops, 'noop / editorial 4 / other', dict(other), flush=True)
            return result
        except BaseException as error:
            r.save(out / 'BLOCKER.json', dict(checked=checked, noops=noops, last=last,
                                             error=f'{type(error).__name__}:{error}', checked_at=r.now()))
            raise


def confirm_committed(connection):
    """The CAS committed; a later catalog read failed. Verify it, do not write again."""
    assert (PHASE / 'REMEDIATION_RECEIPT.json').exists() is False
    assert (PHASE / 'ROLLBACK_BEFORE_COMMIT.json').exists()
    system = r.verify_system()
    assert system['system_identifier'] == EXPECTED_SYSTEM_ID
    before = r.load(PHASE / 'BEFORE_CORRECTION.json')
    after = o.snapshot(connection)
    assert_only_active(before, after)
    second = verify_only(connection)
    assert second['changed_rows'] == 0
    after_verify = o.snapshot(connection)
    same(after_verify, after)
    active = connection.execute('SELECT count(*) FROM source_products WHERE active').fetchone()[0]
    before_active = sum(1 for pair in before['product_offer_projections'] if pair[0]['active'])
    assert before_active - active == 4
    receipt = dict(
        operation='ELIGIBILITY_REVOCATION_RETAIN_HISTORY', committed=True, changed_rows=4,
        source_products_changed=4, offers_changed=0, captures_deleted=0, observations_deleted=0,
        missed_crawls_changed=0, idempotent_second_execution_changed_rows=0,
        active_before=before_active, active_after=active, active_delta=-4,
        rollback_sha256=r.digest((PHASE / 'ROLLBACK_BEFORE_COMMIT.json').read_bytes()),
        post_commit_catalog_read_failed=True, system=system, checked_at=r.now())
    r.save(PHASE / 'REMEDIATION_RECEIPT.json', receipt)
    r.save(PHASE / 'IDEMPOTENCY_RECEIPT.json', second)
    r.save(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json', dict(
        **after_verify, **system, status='PAGE_ROLE_REMEDIATED_REVISED_BASELINE', checked_at=r.now()))
    if (PHASE / 'BLOCKER.json').exists():
        os.replace(PHASE / 'BLOCKER.json', PHASE / 'POST_COMMIT_CATALOG_READ_FAILURE.json')
    print('PAGE ROLE COMMIT CONFIRMED 4 / offers 0 / idempotency 0', flush=True)
    return receipt


def db_session():
    gate = r.load(PHASE / 'OFFLINE_PREPARATION.json')
    assert gate['status'] == 'PASS' and gate['tests']['fail'] == 0
    started = False
    try:
        start_db()
        started = True
        with connect() as connection:
            apply(connection)
            plan = history()
            write_journals(plan)
            run2(connection)
            o.clients(connection)
    except BaseException as error:
        r.save(PHASE / 'BLOCKER.json', dict(error=f'{type(error).__name__}:{error}', checked_at=r.now()))
        raise
    finally:
        if started:
            receipt = stop_db()
            print('PG55455', receipt['status'], receipt['SAFE_TO_POWER_OFF'], flush=True)


def resume_session():
    """Confirm the committed revocation, then journals and verify-only RUN2."""
    started = False
    try:
        start_db()
        started = True
        with connect() as connection:
            confirm_committed(connection)
            plan = history()
            write_journals(plan)
            run2(connection)
            o.clients(connection)
    except BaseException as error:
        r.save(PHASE / 'BLOCKER.json', dict(error=f'{type(error).__name__}:{error}', checked_at=r.now()))
        raise
    finally:
        if started:
            receipt = stop_db()
            print('PG55455', receipt['status'], receipt['SAFE_TO_POWER_OFF'], flush=True)


def session():
    preflight()
    db_session()


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'preflight':
        preflight()
    elif command == 'session':
        session()
    elif command == 'continue':
        db_session()
    elif command == 'resume':
        resume_session()
    elif command == 'stop':
        stop_db()
    else:
        raise SystemExit('Use preflight, session, continue, or stop')
