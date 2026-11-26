"""No network/SQL. Failed worker futures must not orphan RUNNING queue intents."""
from concurrent.futures import Future
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane import automation_execution as ex

@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def deny(*a,**k):raise AssertionError('OFFLINE_ONLY')
    monkeypatch.setattr('psycopg.connect',deny)
    monkeypatch.setattr('urllib.request.OpenerDirector.open',deny)
    monkeypatch.setattr(ex,'ACTIVE',None)

class Queue:
    lease_seconds=300
    def __init__(self):self.done=[];self.claims=0
    def complete(self,*a,**kw):self.done.append((a,kw))
    def recover_stale(self):return 0
    def claim_next(self):self.claims+=1;raise AssertionError('MUST_NOT_CLAIM_AFTER_FAILURE')

def worker(queue,lease_factory):
    registry=SimpleNamespace(supports=lambda *a:True,resolve=lambda code:lambda *a:{'status':'succeeded'})
    return Orchestrator(None,queue=queue,registry=registry,lease_factory=lease_factory)

def test_lease_startup_error_is_durable_and_completes_queue_intent(tmp_path,monkeypatch):
    q=Queue();failure=[]
    monkeypatch.setattr(ex,'ACTIVE',SimpleNamespace(folder=tmp_path,fail=failure.append))
    @contextmanager
    def lease(domain):raise ConnectionError('startup failed password=SECRET_SENTINEL');yield
    w=worker(q,lease);job=SimpleNamespace(id=145,supplier_code='beka_mak',adapter_name='beka_mak',crawl_type='full',domain='beka-mak.su')
    try:
        with pytest.raises(ConnectionError):w.pool.submit(w._run_job,job).result()
    finally:w.pool.shutdown(wait=True)
    assert q.done[0][0]==(145,'failed') and failure==['AUTOMATION_WORKER_BOUNDARY_FAILED']
    evidence=(tmp_path/'WORKER_FAILURE_145.json').read_text()
    assert 'SECRET_SENTINEL' not in evidence and 'SECRET_SENTINEL' not in q.done[0][1]['error_message']
    assert json.loads(evidence)['diagnostics']['original_exception']['type']=='ConnectionError'

def test_unexpected_future_exception_is_consumed_before_next_claim(monkeypatch,caplog):
    q=Queue();failure=[];monkeypatch.setattr(ex,'ACTIVE',SimpleNamespace(fail=failure.append))
    w=worker(q,lambda domain:None);f=Future();f.set_exception(RuntimeError('unobserved task password=SECRET_SENTINEL'))
    w.active={f}
    try:
        with pytest.raises(RuntimeError,match='unobserved task'):w.tick()
    finally:w.pool.shutdown(wait=True)
    assert not q.claims and failure==['AUTOMATION_WORKER_TASK_FAILED']
    assert 'SECRET_SENTINEL' not in caplog.text

def test_successful_future_does_not_change_existing_dispatch(monkeypatch):
    q=Queue();q.claim_next=lambda:None;w=worker(q,lambda domain:None)
    f=Future();f.set_result(None);w.active={f}
    try:assert w.tick()==0 and not w.active
    finally:w.pool.shutdown(wait=True)


def test_delayed_manual_worker_cannot_start_after_job_was_closed():
    from universal_supplier.control_plane.manual_collection import MANUAL_EXECUTION
    q=Queue()
    q.heartbeat=lambda job_id:False
    lease=SimpleNamespace(acquired=True)
    from contextlib import nullcontext
    w=worker(q,lambda domain:nullcontext(lease))
    w.registry.resolve=lambda code:pytest.fail('Closed job must never reach the runner')
    job=SimpleNamespace(id=176,adapter_name='beka_mak',crawl_type='full',domain='beka-mak.su',
                         settings_snapshot={'manual_collection':True})
    token=MANUAL_EXECUTION.set(job.id)
    try:w._run_job(job)
    finally:
        MANUAL_EXECUTION.reset(token)
        w.pool.shutdown(wait=True)
    assert not q.done
