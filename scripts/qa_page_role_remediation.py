"""Fresh FULL QA after the four-row eligibility revocation. No repairs and no writes."""
from collections import Counter
from dataclasses import asdict
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from scripts import qa_ltt_uss_latest as q
from scripts import qa_ltt_uss_revised as guard
from scripts.execute_page_role_remediation import (
    FOUR, IDS, PHASE, HOT, connect, history, start_db, stop_db)
from scripts.final_hotspot36 import B, D, P, S, T, r, o
from scripts.qa_ltt_uss_final_packets import source_packet
from scripts.verify_inverse97_resume import AUDIT, same

F = PHASE / 'FRESH_FULL_QA'
q.OUT = F
q.PLAN = PHASE / 'EFFECTIVE_ROW_PLAN.json'
q.BASELINE = F / 'BASELINE_WITH_COUNTERS.json'
EDITORIAL = {('ltt_msk', spec['index']) for spec in FOUR}


def preflight():
    assert not F.exists(), 'Never resume a prior QA sample'
    run = r.load(PHASE / 'RUN2/RESULT.json')
    assert run['status'] == 'PASS' and run['scope'] == 2634 and run['exact_noop_persisted'] == 2449
    assert run['non_product_editorial'] == 4 and run['sequence_delta'] == 0
    plan = r.load(q.PLAN)['rows']
    assert len(plan) == 2634
    for entry in plan:
        assert r.digest((o.ROOT / entry['journal']).read_bytes()) == entry['journal_sha256']
    base = r.load(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json')
    assert base['totals'] == dict(source_products=2453, offers=2453,
                                  supplier_http_captures=2533, offer_commercial_observations=2533)
    F.mkdir(parents=True)
    r.save(q.BASELINE, dict(base, sources=run['sources']))
    r.save(F / 'OFFLINE_PREFLIGHT.json', dict(
        status='PASS', fresh_from_zero=True, prior_passes_used=False,
        resumed_samples_rejected=['12/400', '8/400', '16/400', '24/400', '2/400'],
        checked_at=r.now()))
    print('PAGE ROLE QA PREFLIGHT PASS', flush=True)


def structural():
    assert r.load(F / 'OFFLINE_PREFLIGHT.json')['status'] == 'PASS'
    assert not (F / 'BEFORE_QA.json').exists()
    plan = r.load(q.PLAN)['rows']
    counts = {supplier: Counter() for supplier in r.SOURCES}
    checked = persisted = 0
    last = None
    started = False
    try:
        start_db()
        started = True
        with guard.guard() as network, connect() as raw:
            raw.execute('SET default_transaction_read_only=on')
            raw.execute("SET application_name='ltt_uss_page_role_full_qa_readonly'")
            assert raw.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
            c = guard.ReadOnlyConnection(raw)
            repo = r.PostgresRepository.__new__(r.PostgresRepository)
            repo.connection = c
            system = r.verify_system()
            before = o.snapshot(c)
            same(before, r.load(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json'))
            r.save(F / 'BEFORE_QA.json', dict(**before, **system, checked_at=r.now()))
            inactive = c.execute(
                'SELECT id, active, missed_crawls FROM source_products WHERE id = ANY(%s) ORDER BY id',
                (list(IDS),)).fetchall()
            assert inactive == [(225, False, 0), (226, False, 0), (227, False, 0), (228, False, 0)]
            try:
                for entry in plan:
                    last = entry
                    path = o.ROOT / entry['journal']
                    assert r.digest(path.read_bytes()) == entry['journal_sha256']
                    row = r.load(path)
                    outcome, product, capture = o.extract(row)
                    assert outcome == entry['outcome']
                    counts[row['supplier']][outcome] += 1
                    ids = None
                    if product is not None:
                        ids = r.check_observation(repo, product, capture)
                        assert ids == row['db_verification']
                        r.verify_persisted(repo, product, capture)
                        joined = c.execute(
                            '''SELECT row_to_json(p), row_to_json(offer), row_to_json(cap), row_to_json(b)
                               FROM source_products p
                               JOIN offers offer ON offer.source_product_id=p.id
                               JOIN supplier_http_captures cap ON cap.source_product_id=p.id
                               JOIN offer_commercial_observations b ON b.capture_id=cap.id AND b.offer_id=offer.id
                               WHERE b.id=%s''', (ids['observation_id'],)).fetchall()
                        assert len(joined) == 1
                        db_product, db_offer, db_capture, db_observation = joined[0]
                        assert db_product['active'] is True and db_product['id'] not in IDS
                        r.save(F / 'PERSISTED_ROWS' / row['supplier'] / path.name, dict(
                            entry=entry, parsed_product=asdict(product), product=db_product, offer=db_offer,
                            capture=db_capture, observation=db_observation, status='AUTOMATED_TRACE_PASS',
                            semantic_approval=False))
                        persisted += 1
                    r.save(F / 'STRUCTURAL_ROWS' / row['supplier'] / path.name, dict(
                        **entry, db_verification=ids, status='STRUCTURAL_PASS', semantic_approval=False))
                    checked += 1
                    if checked % 200 == 0:
                        print(f'QA STRUCTURAL {checked}/2634 persisted {persisted}', flush=True)
                assert checked == 2634 and persisted == 2449
                expected = {supplier: Counter(values) for supplier, values in r.load(q.BASELINE)['sources'].items()}
                assert counts == expected
                after = o.snapshot(c)
                same(after, before)
                r.save(F / 'STRUCTURAL_AUDIT.json', dict(
                    status='PASS', rows_checked=checked, persisted_automated_checked=persisted,
                    sources=counts, unexpected_persistence=0, checked_at=r.now()))
                r.save(F / 'HTTP_ZERO_PROOF.json', dict(**network, sql_writes=0, read_only_sql_enforced=True))
                r.save(F / 'AFTER_STRUCTURAL.json', dict(**after, checked_at=r.now()))
            except BaseException as error:
                r.save(F / 'STRUCTURAL_BLOCKER.json', dict(
                    row=last, checked=checked, persisted_checked=persisted,
                    error=f'{type(error).__name__}:{error}', checked_at=r.now()))
                raise
    finally:
        if started:
            print('QA SHUTDOWN', stop_db()['SAFE_TO_POWER_OFF'], flush=True)
    print('QA STRUCTURAL PASS 2634/2449', flush=True)


def packets():
    assert r.load(F / 'STRUCTURAL_AUDIT.json')['rows_checked'] == 2634
    assert not (F / 'FRESH_400_SAMPLE_MANIFEST.json').exists()
    mandatory = set()
    for path in (B / 'INVERSE_KIND_97_MANIFEST.json', P / 'PRODUCT_KIND_29_MANIFEST.json',
                 T / 'DISK_ALIAS_10_MANIFEST.json', S / 'FINAL_HOTSPOT36_MANIFEST.json'):
        for row in r.load(path)['rows']:
            mandatory.add((row['supplier'], row['index']))
    mandatory |= {('uss_stanko', index) for index in q.FIXED}
    mandatory |= {('ltt_msk', index) for index in (64, 358, 359, 165, 166, 167, 168, 173, 174, 175, 176)}
    mandatory |= {('ltt_sib', index) for index in (941, 958, 959, 1085)}
    mandatory |= {('ltt_yug', 386), ('uss_stanko', 350)}
    mandatory -= EDITORIAL
    units = {row[0] for row in r.load(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json')['sib_units']}
    seed = 'FRESH_PAGE_ROLE_QA_V1\0' + r.digest((PHASE / 'PAGE_ROLE_REVISED_BASELINE.json').read_bytes())
    population = {supplier: [] for supplier in r.SOURCES}
    for entry in r.load(q.PLAN)['rows']:
        if entry['outcome'] != 'PERSISTED':
            continue
        path = F / 'PERSISTED_ROWS' / entry['supplier'] / (o.ROOT / entry['journal']).name
        db = r.load(path)
        product = db['parsed_product']
        kind = product['enrichment_evidence']['product_kind']
        assert kind == db['product']['raw_data']['enrichment_evidence']['product_kind']
        tags = [str(product[name]) for name in ('source_category', 'source_brand', 'availability')]
        tags += [product['price']['state'], product['price']['currency'] or '', kind['product_kind']]
        must = ((entry['supplier'], entry['index']) in mandatory or product['price']['currency'] == 'USD'
                or (entry['supplier'] == 'ltt_sib' and entry['external_id'] in units)
                or (entry['supplier'] == 'ltt_yug' and entry['external_id'] in {'312', '297'}))
        population[entry['supplier']].append(dict(
            entry, persisted_record=str(path.relative_to(F)), mandatory=must, tags=tags,
            rank=r.digest((seed + '\0' + entry['supplier'] + '\0' + entry['external_id']).encode())))
    selected = []
    for supplier, rows in population.items():
        chosen = [row for row in rows if row['mandatory']]
        assert len(chosen) <= 100, (supplier, len(chosen))
        rest = [row for row in rows if not row['mandatory']]
        freq = Counter(tag for row in rows for tag in row['tags'])
        covered = {tag for row in chosen for tag in row['tags']}
        while len(chosen) < 100:
            best = min(rest, key=lambda row: (-sum(1 / freq[tag] for tag in row['tags'] if tag not in covered), row['rank']))
            chosen.append(best)
            rest.remove(best)
            covered.update(best['tags'])
        selected.extend(sorted(chosen, key=lambda row: row['rank']))
    assert len(selected) == 400
    assert Counter(row['supplier'] for row in selected) == Counter(dict(ltt_msk=100, ltt_sib=100, ltt_yug=100, uss_stanko=100))
    r.save(F / 'FRESH_400_SAMPLE_MANIFEST.json', dict(
        total=400, counts=dict(Counter(row['supplier'] for row in selected)), seed=seed,
        rows=selected, prior_semantic_pass_used=False, status='SELECTED_NOT_REVIEWED'))
    with guard.guard() as network:
        for entry in selected:
            r.save(F / 'SEMANTIC_PACKETS' / entry['supplier'] / (o.ROOT / entry['journal']).name, source_packet(entry))
        r.save(F / 'PACKET_HTTP_ZERO_PROOF.json', network)
    print('QA 400 PACKETS READY', flush=True)


def facts():
    from scripts import qa_ltt_uss_latest_semantic_facts as semantic
    semantic.OUT = F
    compact = []
    for entry in r.load(F / 'FRESH_400_SAMPLE_MANIFEST.json')['rows']:
        fact = semantic.read(entry)
        product = r.load(F / entry['persisted_record'])['parsed_product']
        fact['source_category'] = product['source_category']
        fact['semantic_decision'] = None
        r.save(F / 'SEMANTIC_FACTS' / entry['supplier'] / f"{entry['index']:06d}.json", fact)
        compact.append(dict(
            supplier=entry['supplier'], index=entry['index'], external_id=entry['external_id'],
            title=fact['title'], category=product['source_category'],
            kind=fact['kind']['product_kind'], confidence=fact['kind']['confidence'],
            price_raw=fact['source_price']['raw'], price_state=fact['parsed_price']['state'],
            amount=fact['parsed_price']['current_price'], currency=fact['parsed_price']['currency'],
            availability=fact['parsed_availability'], issues=fact['issues'], url=entry['url']))
    r.save(F / 'COMPACT_REVIEW.json', dict(rows=compact, reviewed=0, status='FACTS_READY_NOT_REVIEWED'))
    print('QA FACTS', len(compact), 'issue rows', sum(bool(row['issues']) for row in compact), flush=True)


def cohorts():
    assert r.load(F / 'STRUCTURAL_AUDIT.json')['rows_checked'] == 2634
    plan = {(entry['supplier'], entry['index']): entry for entry in r.load(q.PLAN)['rows']}
    current = {pair[0]['id']: pair for pair in r.load(PHASE / 'PAGE_ROLE_REVISED_BASELINE.json')['product_offer_projections']}
    previous = {pair[0]['id']: pair for pair in r.load(HOT / 'HOTSPOT36_REVISED_BASELINE.json')['product_offer_projections']}
    groups = {
        'VALID_MACHINE362': [row for row in r.load(r.BASE / 'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows']
                             if row['semantic_decision'] == 'VALID_MACHINE'],
        'OPERATOR106': [row for row in r.load(AUDIT / 'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows']
                        if row['semantic_decision'] == 'OPERATOR_REVIEW_REQUIRED'],
        'INVERSE97': r.load(B / 'INVERSE_KIND_97_MANIFEST.json')['rows'],
        'PREVIOUS29': r.load(P / 'PRODUCT_KIND_29_MANIFEST.json')['rows'],
        'DISK10': r.load(T / 'DISK_ALIAS_10_MANIFEST.json')['rows'],
        'HOTSPOT36': r.load(S / 'FINAL_HOTSPOT36_MANIFEST.json')['rows'],
        'TRUE_DISK137': r.load(D / 'DISK_ALIAS_VALID_REGRESSION_COHORT.json')['rows'],
    }
    expected_counts = dict(VALID_MACHINE362=362, OPERATOR106=106, INVERSE97=97, PREVIOUS29=29,
                           DISK10=10, TRUE_DISK137=137, HOTSPOT36=36)
    for label, rows in groups.items():
        checked = []
        for original in rows:
            row = dict(original, **original['entry']) if 'entry' in original else original
            entry = plan[row['supplier'], row['index']]
            if (row['supplier'], row['index']) in EDITORIAL:
                assert label == 'OPERATOR106'
                product = current[row['source_product_id'] if 'source_product_id' in row else entry_product_id(current, entry)][0]
                prior = previous[product['id']][0]
                assert product['active'] is False and product['missed_crawls'] == 0
                assert product['raw_data']['enrichment_evidence']['product_kind'] == prior['raw_data']['enrichment_evidence']['product_kind']
                checked.append(dict(entry=entry, status='ELIGIBILITY_REVOKED_KIND_UNCHANGED'))
                continue
            db = r.load(F / 'PERSISTED_ROWS' / entry['supplier'] / (o.ROOT / entry['journal']).name)
            kind = db['parsed_product']['enrichment_evidence']['product_kind']
            expected = row.get('new_kind') or row.get('current_kind') or row['raw_enrichment_evidence']['product_kind']
            assert kind == expected == db['product']['raw_data']['enrichment_evidence']['product_kind']
            assert db['product']['active'] is True
            checked.append(dict(entry=entry, current_kind=kind, status='CURRENT_MAPPING_PASS'))
        assert len(checked) == expected_counts[label], (label, len(checked))
        r.save(F / f'{label}_MAPPING.json', dict(status='PASS', count=len(checked), prior_semantic_passes_used=False))
    boundary = []
    for index in range(160, 181):
        entry = plan['ltt_msk', index]
        boundary.append(dict(index=index, external_id=entry['external_id'], outcome=entry['outcome'], url=entry['url']))
    assert [row['outcome'] for row in boundary if row['index'] in {169, 170, 171, 172}] == ['NON_PRODUCT_EDITORIAL_PAGE'] * 4
    assert all(row['outcome'] == 'PERSISTED' for row in boundary if row['index'] not in {169, 170, 171, 172})
    assert plan['ltt_yug', 591]['outcome'] == 'NON_PRODUCT_CATEGORY_PAGE'
    r.save(F / 'PAGE_ROLE_QA.json', dict(
        status='PASS', editorial='4/4', yug_category_591='NON_PRODUCT_CATEGORY_PAGE',
        boundary_persisted=sum(row['outcome'] == 'PERSISTED' for row in boundary),
        boundary=boundary, article_active=False, new_entities=0))
    print('QA COHORTS AND PAGE ROLE PASS', flush=True)


def entry_product_id(current, entry):
    for product, _offer in current.values():
        if product['external_id'] == entry['external_id'] and product['source_url'] == entry['url']:
            return product['id']
    raise AssertionError('EDITORIAL_PRODUCT_NOT_FOUND')


if __name__ == '__main__':
    {'preflight': preflight, 'structural': structural, 'packets': packets, 'facts': facts, 'cohorts': cohorts}[sys.argv[1]]()
