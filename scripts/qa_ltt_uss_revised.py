"""Fresh revised-baseline FULL QA: offline inputs, read-only SQL, no writer."""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from collections import Counter
from unittest.mock import patch
from pathlib import Path
import re
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import correct_uss_commercial_tables as f
o=f.o
r=f.r
OUT=r.BASE/'FULL_QA_REVISED_2026-10-15'


@contextmanager
def guard():
    def deny(*args,**kwargs):raise o.OfflineViolation('QA_SOURCE_PERSISTENCE_PROHIBITED')
    with ExitStack() as stack:
        counters=stack.enter_context(o.offline_guard())
        for name in ('persist_commercial_observation','_persist_passive_observation'):
            stack.enter_context(patch.object(r.PostgresRepository,name,deny))
        yield counters


def readonly_sql(query):
    o.validate_sql(query)
    source=query if isinstance(query,str) else query.as_string()
    if re.search(r'\bFOR\s+(?:UPDATE|SHARE)|pg_advisory',source,re.I):
        raise o.OfflineViolation('QA_LOCKING_SQL_PROHIBITED')


class ReadOnlyConnection:
    def __init__(self,inner):self.inner=inner
    def execute(self,query,params=None):
        readonly_sql(query)
        return self.inner.execute(query,params)


def structural():
    assert r.load(OUT/'OFFLINE_PREFLIGHT.json')['status']=='PASS'
    assert not (OUT/'STRUCTURAL_RESULT.json').exists(),'New QA cannot overwrite completed audit'
    plan=r.load(f.PHASE/'EFFECTIVE_ROW_PLAN.json')['rows']
    expected={s:Counter() for s in r.SOURCES}
    _,cfg=r.config();last=None;passed=0;persisted=0
    with guard() as network:
      with psycopg.connect(**cfg,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        c.execute("SET application_name='ltt_uss_revised_full_qa_readonly'")
        assert c.execute('SHOW default_transaction_read_only').fetchone()[0]=='on'
        assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        system=r.verify_system();before=o.snapshot(c)
        baseline=r.load(f.PHASE/'REVISED_RUN1_BASELINE_AFTER_QA_FIX.json')
        assert before['fingerprints']==baseline['fingerprints']
        assert r.encoded(before['sequences'])==r.encoded(baseline['sequences'])
        r.save(OUT/'BEFORE_QA.json',dict(**before,**system,checked_at=r.now()))
        repo=r.PostgresRepository.__new__(r.PostgresRepository);repo.connection=ReadOnlyConnection(c)
        try:
            for entry in plan:
                last=entry;path=o.ROOT/entry['journal']
                assert r.digest(path.read_bytes())==entry['journal_sha256']
                row=r.load(path)
                assert row['supplier']==entry['supplier'] and row['index']==entry['index'] and row['url']==entry['url']
                outcome,product,capture=o.extract(row)
                assert outcome==row['outcome']==entry['outcome']
                expected[row['supplier']][outcome]+=1
                ids=None
                if product is not None:
                    assert product.site_internal_id==entry['external_id']
                    ids=r.check_observation(repo,product,capture);assert ids==row['db_verification']
                    r.verify_persisted(repo,product,capture)
                    latest=c.execute('SELECT max(observed_at) FROM offer_commercial_observations WHERE source_product_id=%s',(ids['source_product_id'],)).fetchone()[0]
                    assert latest==capture.observed_at
                    commercial=c.execute('SELECT price_raw,currency,availability_normalized FROM offer_commercial_observations WHERE id=%s',(ids['observation_id'],)).fetchone()
                    assert commercial[0]==product.price.raw_text and (commercial[1] or '')==(product.price.currency or '')
                    assert commercial[2]==__import__('universal_supplier.commercial_persistence',fromlist=['normalize_availability']).normalize_availability(product.availability)
                    persisted+=1
                passed+=1
                r.save(OUT/'STRUCTURAL_ROWS'/row['supplier']/path.name,dict(supplier=row['supplier'],index=row['index'],url=row['url'],outcome=outcome,
                    journal_sha256=entry['journal_sha256'],external_id=row.get('external_id'),db_verification=ids,
                    status='STRUCTURAL_PASS',semantic_correctness_not_implied=True))
                if passed%200==0:
                    r.save(OUT/'CHECKPOINT.json',dict(status='QA_STRUCTURAL_RUNNING',checked=passed,total=2634,persisted_checked=persisted,safe_to_power_off=False,updated_at=r.now()))
                    print(f'QA STRUCTURAL {passed}/2634 persisted{persisted}',flush=True)
            assert passed==2634 and persisted==2440
            assert expected['uss_stanko']==Counter(PERSISTED=214,AMBIGUOUS=127,MULTI_VARIANT=66)
            r.save(OUT/'STRUCTURAL_RESULT.json',dict(status='LAYER_A_STRUCTURAL_PASS',rows_checked=passed,persisted_automated_checked=persisted,sources=expected,missing_evidence=0,broken_references=0,checked_at=r.now()))
        except Exception as error:
            r.save(OUT/'STRUCTURAL_BLOCKER.json',dict(row=last,checked=passed,persisted_checked=persisted,error=f'{type(error).__name__}:{error}',checked_at=r.now()))
            raise
        finally:
            after=o.snapshot(c)
            assert not f.verify_unchanged(before,after)
            r.save(OUT/'AFTER_STRUCTURAL.json',dict(**after,checked_at=r.now()))
            r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,source_persistence_calls=0,sql_writes=0,read_only_sql_enforced=True))
    print('Layer A PASS:2634structural/2440persisted automated; no writes. Layer B still pending.',flush=True)


if __name__=='__main__':structural()
