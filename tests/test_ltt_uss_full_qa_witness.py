"""FULL QA assertions, including the unmodified parser's blocking regression."""
import urllib.request
import pytest
from scripts import qa_ltt_uss_frozen_witness as qa


def test_qa_supplier_network_is_prohibited():
    with qa.qa_guard() as counters:
        with pytest.raises(qa.offline.OfflineViolation,match='TRANSPORT_PROHIBITED'):
            urllib.request.urlopen('https://uss-stanko.com/')
    assert counters['prohibited_transport_attempts']==1


def test_qa_source_persistence_is_prohibited():
    repo=qa.offline.r.PostgresRepository.__new__(qa.offline.r.PostgresRepository)
    with qa.qa_guard():
        with pytest.raises(qa.offline.OfflineViolation,match='PERSISTENCE_PROHIBITED'):
            repo.persist_commercial_observation(None,None)


def test_saved_airstar_single_product_evidence_is_explicit():
    with qa.qa_guard() as network:
        fact=qa.airstar_witness(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    assert fact['independent_product_tables']==1
    assert fact['expected_price']=='189360' and fact['expected_currency']=='RUB'
    assert fact['model']=='Airstar 703/270/10 H'
    assert fact['raw_availability']=='под заказ' and fact['expected_availability']=='PreOrder'
    assert fact['manufacturer_article']=='202 8753'
    assert network['prohibited_transport_attempts']==0


def test_single_explicit_airstar_card_must_not_be_ambiguous():
    # Intentionally NOT xfail: this is the actual QA blocker, not a parser repair.
    with qa.qa_guard():
        fact=qa.airstar_witness(baseline_seal='DISK_ALIAS_10_CODE_SEAL', proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    assert fact['actual_gate_status']=='SUCCESS', (
        'Single own-model/article/price/stock table excluded by table.data-only selector: '+repr(fact))
    assert fact['actual_parser_price']=='189360'
    assert fact['actual_parser_availability']=='PreOrder'
