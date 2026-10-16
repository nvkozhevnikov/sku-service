"""Focused RUN2 prohibition/no-op/deterministic frozen-evidence regressions."""
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock
import socket
import urllib.request
import pytest
from scripts import run_ltt_uss_run2_offline as offline


@pytest.mark.parametrize('operation', ['urlopen','opener','client','socket','connect_ex','create_connection'])
def test_offline_transport_prohibition(operation):
    from universal_supplier.http_capture import UrllibPublicHttpClient
    with offline.offline_guard() as receipt:
        with pytest.raises(offline.OfflineViolation, match='TRANSPORT_PROHIBITED'):
            if operation == 'urlopen': urllib.request.urlopen('https://uss-stanko.com/')
            elif operation == 'opener': urllib.request.build_opener()
            elif operation == 'client': UrllibPublicHttpClient().get('https://ltt-yug.ru/',timeout_seconds=1)
            elif operation == 'create_connection': socket.create_connection(('uss-stanko.com',443))
            else:
                with socket.socket() as sock:
                    getattr(sock,'connect_ex' if operation=='connect_ex' else 'connect')(('8.8.8.8',443))
        assert receipt['prohibited_transport_attempts'] == 1
        assert receipt['supplier_http_gets'] == receipt['supplier_http_heads'] == receipt['robots_http'] == 0


@pytest.mark.parametrize('query', ['INSERT INTO offers DEFAULT VALUES','UPDATE offers SET price=0',
    'DELETE FROM offers','SELECT nextval(\'offers_id_seq\')','SELECT setval(\'offers_id_seq\',1)',
    'WITH x AS (DELETE FROM offers RETURNING *) SELECT * FROM x','SELECT 1; DELETE FROM offers'])
def test_mutation_blocked_before_database(query):
    cursor = Mock()
    with pytest.raises(offline.OfflineViolation,match='MUTATING_SQL_PROHIBITED'):
        offline.NoMutationCursor(cursor).execute(query)
    cursor.execute.assert_not_called()


def test_exact_noop_actual_writer_with_only_selects():
    # Exercise ordinary repository persistence through the RUN2 SQL filter.
    from tests.test_ltt_uss_usd_bounded import r
    run = offline.frozen_run(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    row = r.load(run.journal_path('uss_stanko',178))
    _,product,capture = offline.extract(row)
    cursor = Mock()
    cursor.fetchone.side_effect = [(4,), (22,None,capture.observed_at,{}), (88,)]
    connection = Mock()
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    connection.transaction.return_value.__enter__ = Mock()
    connection.transaction.return_value.__exit__ = Mock(return_value=False)
    # Use a simple actual context cursor so SQL goes through NoMutationCursor.
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection.cursor.return_value = cursor
    repo = r.PostgresRepository.__new__(r.PostgresRepository)
    repo.connection = offline.NoMutationConnection(connection)
    receipt = repo.persist_commercial_observation(product,capture)
    assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
    assert receipt['capture_id'] == 88
    assert all(v==0 for v in asdict(receipt['ingest_counts']).values())
    assert all(call.args[0].lstrip().startswith('SELECT ') for call in cursor.execute.call_args_list)


@pytest.mark.parametrize('supplier,index,expected', [
    ('ltt_yug',591,'NON_PRODUCT_CATEGORY_PAGE'),
    ('uss_stanko',0,'AMBIGUOUS'),
    ('uss_stanko',178,'PERSISTED'),
    ('ltt_sib',846,'PERSISTED')])
def test_frozen_terminal_usd_and_unit_regressions(supplier,index,expected):
    run = offline.frozen_run(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    row = offline.r.load(run.journal_path(supplier,index))
    with offline.offline_guard() as network:
        outcome,product,capture = offline.extract(row)
    assert outcome == row['outcome'] == expected
    assert network['prohibited_transport_attempts'] == 0
    if supplier == 'uss_stanko' and index == 178:
        assert product.price.current_price == 21312 and product.price.currency == 'USD'
        assert product.price.raw_text == '21 312 USD'
    if supplier == 'ltt_sib':
        assert product.price.currency == 'RUB' and 'бухта' in product.price.raw_text
        assert product.price.diagnostics == ('sales_unit_raw:бухта',)


def test_yug_distinct_ids_and_multivariant_stable():
    run = offline.frozen_run(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    ids = {}
    multi = None
    uf200 = None
    for _,row in offline.journals(run):
        if row['supplier']=='ltt_yug' and row.get('external_id') in {'312','297'}:
            _,product,_ = offline.extract(row)
            ids[product.site_internal_id] = product
        if row['supplier']=='uss_stanko' and row['outcome']=='MULTI_VARIANT' and multi is None:
            multi = row
        if row.get('external_id')=='uss:mach/frez/uf200.htm': uf200=row
    assert set(ids)=={'312','297'} and ids['312'].source_url != ids['297'].source_url
    assert offline.extract(multi) == ('MULTI_VARIANT',None,None)
    _,product,_ = offline.extract(uf200)
    assert product.price.current_price==31212 and product.price.currency=='USD'
    assert product.price.raw_text=='31 212 USD'


def test_changed_terminal_fails_instead_of_becoming_product():
    run=offline.frozen_run(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    row=offline.r.load(run.journal_path('uss_stanko',0))
    row['outcome']='PERSISTED'
    with pytest.raises(AssertionError,match='TERMINAL_DRIFT'):
        offline.extract(row)


def test_preparation_resume_requires_identical_existing_seal(tmp_path,monkeypatch):
    monkeypatch.setattr(offline,'OUT',tmp_path)
    monkeypatch.setattr(offline,'frozen_run',lambda:SimpleNamespace())
    monkeypatch.setattr(offline,'inventory',lambda:{'a':dict(sha256='changed')})
    offline.r.save(tmp_path/'FROZEN_INPUTS.json',{'a':dict(sha256='original')})
    with pytest.raises(AssertionError,match='Frozen seal drift'):
        offline.prepare(resume=True)
    assert offline.r.load(tmp_path/'FROZEN_INPUTS.json')['a']['sha256']=='original'


def test_preparation_cannot_repeat_after_database_stage(tmp_path,monkeypatch):
    monkeypatch.setattr(offline,'OUT',tmp_path)
    offline.r.save(tmp_path/'BEFORE_RUN2.json',{})
    with pytest.raises(AssertionError,match='Database stage already started'):
        offline.prepare(resume=True)
