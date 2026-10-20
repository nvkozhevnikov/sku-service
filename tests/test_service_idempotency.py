"""Offline cursor-contract/race regressions; real PostgreSQL QA remains separately gated."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import pytest
from universal_supplier.postgres import PostgresRepository as Repo
from test_partner_history_fix import fixture_card

class SeenCursor:
    def __init__(self,history=()):self.history=list(history);self.lock=threading.Lock();self.last=None
    def execute(self,q,args):
        assert 'ON CONFLICT(source_product_id)' in q
        assert 'WHERE NOT (missing_observations.history @> EXCLUDED.history)' in q
        assert 'history || EXCLUDED.history' in q
        product,at,run,event=args;event=json.loads(event)
        with self.lock:
            if event not in self.history:self.history.append(event);self.last=(at,run)


def seen(c,at='2026-10-20T00:00:00Z',run=12):Repo._record_seen_observation(c,3,run,at)

def test_seen_retry_after_later_seen_and_missing_preserves_all_history():
    c=SeenCursor();seen(c);seen(c,'2026-10-21T00:00:00Z');missing={'evidence_type':'HTTP_404','crawl_run_id':13}
    c.history.append(missing);state=list(c.history);last=c.last
    seen(c)
    assert c.history==state and c.last==last and missing in c.history

@pytest.mark.parametrize('at,run',[('2026-10-21T00:00:00Z',12),('2026-10-20T00:00:00Z',13)])
def test_new_observation_or_run_remains_distinct(at,run):
    c=SeenCursor();seen(c);seen(c,at,run);assert len(c.history)==2

def test_seen_concurrent_exact_retry_has_one_event():
    c=SeenCursor()
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:seen(c),range(40)))
    assert len(c.history)==1

class QualityStore:
    def __init__(self):self.lock=threading.RLock();self.rows=[];self.errors=[];self.calls=[];self.fail_once=False;self.previous=None
    @contextmanager
    def cursor(self):
        c=QualityCursor(self)
        try:yield c
        finally:
            if c.locked:self.lock.release()

class QualityCursor:
    def __init__(self,db):self.db=db;self.answer=None;self.locked=False
    def execute(self,q,args):
        self.db.calls.append(q)
        if 'FOR UPDATE' in q:
            self.db.lock.acquire();self.locked=True
        elif 'SELECT d.products_seen' in q:self.answer=self.db.previous
        elif 'SELECT id FROM data_quality_snapshots' in q:
            assert self.locked
            target=(*args[:4],json.loads(args[4]),args[5],args[6]);self.answer=next(((i,) for i,x in enumerate(self.db.rows) if x==target),None)
        elif 'INSERT INTO data_quality_snapshots' in q:
            assert self.locked
            if self.db.fail_once:self.db.fail_once=False;raise RuntimeError('interrupted')
            self.db.rows.append((*args[:4],json.loads(args[4]),args[5],args[6]))
        elif 'INSERT INTO crawl_errors' in q or 'INSERT INTO diagnostic_evidence_snapshots' in q:self.db.errors.append((q,args))
        else:raise AssertionError(q)
    def fetchone(self):return self.answer

def quality(db,card=None,run=12,signals=()):
    with db.cursor() as c:return Repo._record_quality_snapshot(c,1,run,[card or fixture_card()],1,schema_signals=list(signals),crawl_settings={},base_url='https://partner-st.ru/')

def test_quality_equal_profile_does_not_insert_or_rewrite_existing_duplicates():
    db=QualityStore();quality(db);db.rows.append(db.rows[0]);old=list(db.rows)
    result=quality(db);assert db.rows==old and not result.critical
    assert 'FOR UPDATE' in db.calls[0]

def test_changed_profile_and_new_run_are_retained():
    db=QualityStore();quality(db);quality(db,replace(fixture_card(),price=None));quality(db,run=13)
    assert len(db.rows)==3

def test_interrupted_profile_insert_can_be_retried():
    db=QualityStore();db.fail_once=True
    with pytest.raises(RuntimeError,match='interrupted'):quality(db)
    quality(db);quality(db);assert len(db.rows)==1

def test_concurrent_same_profile_serializes_check_and_insert():
    db=QualityStore()
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:quality(db),range(40)))
    assert len(db.rows)==1

def test_critical_diagnostics_not_duplicated_and_allow_missing_retained():
    db=QualityStore();first=quality(db,signals=('parser_error',));n=len(db.errors)
    second=quality(db,signals=('parser_error',))
    assert first.critical and second.critical and not first.allow_missing_consequences
    assert first==second and len(db.rows)==1 and len(db.errors)==n and n>0
