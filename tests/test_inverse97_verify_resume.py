"""Real repository no-op with lock-compatible session and unchanged SQL guard."""
from unittest.mock import Mock
from dataclasses import asdict
import inspect
import pytest
from scripts import verify_inverse97_resume as v

def connection(answers):
    cursor=Mock();cursor.fetchone.side_effect=answers
    cursor.__enter__=Mock(return_value=cursor);cursor.__exit__=Mock(return_value=False)
    raw=Mock();raw.cursor.return_value=cursor
    raw.execute.return_value.fetchone.return_value=('off',)
    raw.transaction.return_value.__enter__=Mock();raw.transaction.return_value.__exit__=Mock(return_value=False)
    return raw,cursor

def frozen():
    run=v.o.frozen_run(baseline_seal='DISK_ALIAS_10_CODE_SEAL',proposed_transition='PAGE_ROLE_GATE_CODE_SEAL')
    _,product,capture=v.o.extract(v.r.load(run.journal_path('uss_stanko',178)))
    return product,capture

def test_actual_exact_noop_allows_for_update_without_database_setting_changes():
    product,capture=frozen();raw,cur=connection([(4,),(22,None,capture.observed_at,{}),(88,)])
    repo=v.guarded_repository(raw);assert type(repo.connection) is v.o.NoMutationConnection
    result=repo.persist_commercial_observation(product,capture)
    assert result['exact_noop'] and result['capture_id']==88
    assert not result['observation_created'] and not result['projection_updated']
    assert all(n==0 for n in asdict(result['ingest_counts']).values())
    queries=[call.args[0] for call in cur.execute.call_args_list]
    assert any('FOR UPDATE' in sql for sql in queries)
    assert all(sql.lstrip().startswith('SELECT ') for sql in queries)
    assert [x.args[0] for x in raw.execute.call_args_list]==['SHOW transaction_read_only','SHOW default_transaction_read_only']
    raw.commit.assert_not_called()

def test_unexpected_nonnoop_insert_fails_before_database():
    product,capture=frozen();raw,cur=connection([None])
    repo=v.guarded_repository(raw)
    with pytest.raises(v.o.OfflineViolation,match='MUTATING_SQL_PROHIBITED'):
        repo.persist_commercial_observation(product,capture)
    assert len(cur.execute.call_args_list)==1
    assert 'SELECT id FROM suppliers' in cur.execute.call_args.args[0]
    raw.commit.assert_not_called()

@pytest.mark.parametrize('sql',[
    'INSERT INTO offers DEFAULT VALUES','UPDATE offers SET price=0','DELETE FROM offers',
    'CREATE TABLE forbidden(id int)','ALTER TABLE offers ADD x int','DROP TABLE offers',
    'TRUNCATE offers',"SELECT nextval('offers_id_seq')","SELECT setval('offers_id_seq',1)",
    'WITH x AS (DELETE FROM offers RETURNING *) SELECT * FROM x','SELECT 1; DELETE FROM offers',
    'CALL forbidden()','DO $$BEGIN END$$','COPY offers TO STDOUT','MERGE INTO offers USING offers x ON false WHEN MATCHED THEN DELETE'])
def test_mutations_never_reach_database(sql):
    raw,_=connection([]);repo=v.guarded_repository(raw);raw.execute.reset_mock()
    with pytest.raises(v.o.OfflineViolation,match='MUTATING_SQL_PROHIBITED'):repo.connection.execute(sql)
    raw.execute.assert_not_called()

def test_readonly_default_fails_closed_without_reconfiguration():
    raw,_=connection([]);raw.execute.return_value.fetchone.return_value=('on',)
    with pytest.raises(AssertionError):v.guarded_repository(raw)
    assert len(raw.execute.call_args_list)==1

def test_verify_harness_has_no_correction_entrypoint_or_readonly_set():
    text=inspect.getsource(v)
    assert 'inverse_kind_metadata_correction' not in text and 'execute_inverse_kind97' not in text
    assert 'correction.apply' not in text
    assert 'SET default_transaction_read_only' not in text and 'SET transaction_read_only' not in text
