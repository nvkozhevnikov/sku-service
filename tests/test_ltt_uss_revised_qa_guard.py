import pytest
import urllib.request
from scripts import qa_ltt_uss_revised as qa


def test_qa_network_prohibited():
    with qa.guard():
        with pytest.raises(qa.o.OfflineViolation,match='TRANSPORT_PROHIBITED'):
            urllib.request.urlopen('https://uss-stanko.com/')


def test_qa_persistence_prohibited():
    with qa.guard():
        with pytest.raises(qa.o.OfflineViolation,match='PERSISTENCE_PROHIBITED'):
            qa.r.PostgresRepository.__new__(qa.r.PostgresRepository).persist_commercial_observation(None,None)


@pytest.mark.parametrize('query',['INSERT INTO offers DEFAULT VALUES','UPDATE offers SET price=0','DELETE FROM offers',
    'CREATE TABLE x(a int)',"SELECT nextval('offers_id_seq')","SELECT setval('offers_id_seq',1)",'SELECT 1 FOR UPDATE'])
def test_qa_sql_mutations_sequences_and_locks_prohibited(query):
    with pytest.raises(qa.o.OfflineViolation):qa.readonly_sql(query)


def test_qa_plain_select_allowed():
    qa.readonly_sql('SELECT p.id FROM source_products p WHERE p.external_id=%s')
