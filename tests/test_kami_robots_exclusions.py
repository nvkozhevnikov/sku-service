"""Offline KAMI terminal exclusions, no HTTP or database connections."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import kami_reconciliation_contract as contract


@pytest.fixture
def fixture(tmp_path):
    robot = ('User-agent: *\n' + contract.RULE + '\n').encode()
    robot_sha = hashlib.sha256(robot).hexdigest()
    folder = tmp_path / 'saved'
    folder.mkdir()
    (folder / (robot_sha + '.capture')).write_bytes(robot)
    rows = [{'url': u, 'site_id': site, 'name': 'Machine ' + site,
             'terminal_status': 'ROBOTS_EXCLUDED', 'detail_identity_proven': False,
             'listing_evidence_indexes': [0]} for u, site in contract.EXCLUSIONS.items()]
    html = ''.join('<div class="productCard" data-product-id="' + r['site_id'] + '">'
                  '<a class="productCard__name" href="' + r['url'] + '">' + r['name'] +
                  '</a></div>' for r in rows).encode()
    digest = hashlib.sha256(html).hexdigest()
    (folder / 'listing.capture').write_bytes(html)
    ledger = {'status': 'OPERATOR_CONFIRMED_ROBOTS_EXCLUSIONS',
              'authorization': 'OPERATOR-CONFIRMED 2026-10-05', 'robots_sha256': robot_sha,
              'reason': contract.RULE, 'detail_GET_performed': False,
              'excluded_from_proven_detail_product_identities': True, 'rows': rows,
              'shared_listing_evidence': [{'category_page': 'https://www.stanki.ru/catalog/x/',
                  'capture': 'saved/listing.capture', 'sha256': digest, 'observed_at': '2026-10-05T09:00:00Z'}]}
    def save():
        (tmp_path / 'ROBOTS_EXCLUSIONS.json').write_text(json.dumps(ledger), encoding='utf-8')
    save()
    state = {'completed': {}, 'queue': list(contract.EXCLUSIONS) + ['permitted'],
             'captures': [], 'cards': {}}
    with patch.object(contract, 'ROBOTS_SHA', robot_sha):
        yield tmp_path, ledger, state, save


def project(fixture):
    base, _, state, _ = fixture
    proof = contract.validate_exclusions(base, state)
    contract.project_exclusions(state, proof)
    contract.validate_exclusions(base, state)
    return state


def test_terminally_closes_exactly_two_urls(fixture):
    state = project(fixture)
    assert len(state['completed']) == 2
    assert state['queue'] == ['permitted']
    assert {r['route'] for r in state['completed'].values()} == {'ROBOTS_EXCLUDED'}


def test_not_a_proven_product_identity(fixture):
    state = project(fixture)
    assert not state['cards']
    assert not contract.actionable_completed(state)
    assert all('external_id' not in r for r in state['completed'].values())


def test_not_unresolved(fixture):
    state = project(fixture)
    assert set(r['route'] for r in state['completed'].values()) <= contract.TERMINAL_ROUTES
    assert not set(contract.EXCLUSIONS) & set(state['queue'])


def test_not_stale_missing_new_review_or_conflict(fixture):
    state = project(fixture)
    assert all(r['route'] not in {'SOURCE_STALE_URL', 'MISSING', 'NEW', 'REVIEW', 'CONFLICT'}
               for r in state['completed'].values())


def test_matching_projection_excludes_terminal_rows(fixture):
    state = project(fixture)
    state['completed']['machine'] = {'route': 'PRODUCT_OBSERVED', 'external_id': 'site:1'}
    assert list(contract.actionable_completed(state)) == ['machine']


def test_readiness_and_persistence_cannot_receive_listing_identity(fixture):
    state = project(fixture)
    assert not state['cards']  # verifier emits CARDS solely from this map
    assert all(r['downstream_actionable'] is False for r in state['completed'].values())


@pytest.mark.parametrize('field', ['authorization', 'shared_listing_evidence'])
def test_missing_provenance_fails(fixture, field):
    base, ledger, state, save = fixture
    ledger.pop(field)
    save()
    with pytest.raises(RuntimeError):
        contract.validate_exclusions(base, state)


@pytest.mark.parametrize('status', [200, 404, 403])
def test_any_excluded_detail_get_fails(fixture, status):
    base, _, state, _ = fixture
    state['captures'].append({'url': next(iter(contract.EXCLUSIONS)), 'status': status})
    with pytest.raises(RuntimeError, match='Detail GET'):
        contract.validate_exclusions(base, state)


def test_exactly_two_current_exclusions_reconcile_and_replay_noop(fixture):
    state = project(fixture)
    before = deepcopy(state)
    project(fixture)
    assert state == before


def test_unrelated_non_kami_completed_and_queue_unchanged(fixture):
    _, _, state, _ = fixture
    other = 'https://www.bekamak.com/urun/unchanged/en'
    state['completed'][other] = {'route': 'PRODUCT_OBSERVED', 'external_id': 'old'}
    state['queue'].append(other)
    before = deepcopy(state['completed'][other])
    project(fixture)
    assert state['completed'][other] == before
    assert other in state['queue']


def test_identity_leak_fails(fixture):
    base, _, state, _ = fixture
    state['cards']['site:9967380'] = {'canonical_url': 'https://www.stanki.ru/catalog/x/'}
    with pytest.raises(RuntimeError, match='leaked'):
        contract.validate_exclusions(base, state)


def test_unapproved_third_exclusion_fails(fixture):
    base, ledger, state, save = fixture
    ledger['rows'].append({**ledger['rows'][0], 'url': 'https://www.stanki.ru/catalog/third/'})
    save()
    with pytest.raises(RuntimeError, match='Exactly two'):
        contract.validate_exclusions(base, state)


def test_forged_terminal_projection_fails(fixture):
    base, _, state, _ = fixture
    state['completed'][next(iter(contract.EXCLUSIONS))] = {'route': 'ROBOTS_EXCLUDED'}
    with pytest.raises(RuntimeError, match='projection'):
        contract.validate_exclusions(base, state)


def test_real_saved_exclusion_evidence_offline():
    base = Path(__file__).resolve().parents[1] / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'
    state = {'completed': {}, 'queue': list(contract.EXCLUSIONS), 'cards': {}, 'captures': []}
    proof = contract.validate_exclusions(base, state)
    contract.project_exclusions(state, proof)
    assert len(state['completed']) == 2
    assert not state['queue']
