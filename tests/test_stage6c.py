from datetime import datetime, timezone
import asyncio
from concurrent.futures import wait
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane.adapter_registry import AdapterRegistry
from universal_supplier.control_plane.queue import InMemoryJobQueue, normalized_domain
from universal_supplier.control_plane.rate_control import (
    AsyncDomainRateLimiter, CircuitBreaker, CircuitOpen, DomainRateLimiter, DomainRatePolicy,
    RequestMetrics, request_with_resilience, retry_after_seconds,
)
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.control_plane.scheduler import next_occurrences, validate_schedule
from universal_supplier.control_plane.store import ProductFilters, SnapshotControlPlaneStore, product_where
from universal_supplier.control_plane.web import create_app


ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self): self.value = 0.0; self.sleeps = []
    def monotonic(self): return self.value
    def sleep(self, seconds): self.sleeps.append(seconds); self.value += seconds


class Response:
    def __init__(self, status_code, headers=None): self.status_code=status_code; self.headers=headers or {}


def test_stage6c_migration_is_additive_and_legacy_hashes_are_unchanged():
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))["files"]
    actual = {name: hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest() for name in baseline}
    assert actual == baseline
    sql = (ROOT / "migrations/012_web_control_plane_scheduler.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE crawl_jobs" in sql and "CREATE TABLE supplier_schedules" in sql
    assert "product_identity_decisions" in sql and "timezone" in sql


def test_queue_deduplicates_manual_and_schedule_requests():
    queue = InMemoryJobQueue()
    first = queue.enqueue(7, trigger_type="manual", crawl_type="full", settings_snapshot={"v": 1})
    second = queue.enqueue(7, trigger_type="schedule", crawl_type="full")
    other = queue.enqueue(8, trigger_type="schedule", crawl_type="fast")
    assert first.enqueued and other.enqueued
    assert second.status == "skipped_already_queued" and second.deduplicated_job_id == first.job_id


def test_domains_are_normalized_for_advisory_lock_scope():
    assert normalized_domain("HTTPS://WWW.Example.COM/a") == "example.com"
    assert normalized_domain("https://example.com:443/x") == "example.com"


def test_shared_limiter_spaces_all_starts_and_isolated_domain_does_not_wait():
    clock = FakeClock(); policy = DomainRatePolicy(min_request_interval_seconds=0.25, jitter_max_seconds=0)
    shared = DomainRateLimiter(policy, clock=clock)
    assert [shared.wait(), shared.wait(), shared.wait()] == [0, .25, .25]
    isolated = DomainRateLimiter(policy, clock=clock)
    assert isolated.wait() == 0


def test_optimum_style_async_workers_share_one_domain_start_gate():
    class AsyncFakeClock:
        def __init__(self): self.value=0.0; self.sleeps=[]
        def monotonic(self): return self.value
        async def sleep(self, seconds): self.sleeps.append(seconds); self.value += seconds
    async def exercise():
        clock=AsyncFakeClock(); policy=DomainRatePolicy(min_request_interval_seconds=.15,jitter_max_seconds=0)
        limiter=AsyncDomainRateLimiter(policy,clock=clock)
        delays=await asyncio.gather(*(limiter.wait() for _ in range(3)))
        return delays,clock.sleeps
    delays,sleeps=asyncio.run(exercise())
    assert delays == [0,.15,.15] and sleeps == [.15,.15]


def test_orchestrator_executes_different_domains_in_parallel_with_fake_adapters():
    now=datetime.now(timezone.utc)
    jobs=[CrawlJob(i,i,f"s{i}","fake",f"https://d{i}.invalid","manual","full","running",now,{}) for i in (1,2)]
    class Queue:
        lease_seconds=30
        def claim_next(self): return jobs.pop(0) if jobs else None
        def complete(self,*args,**kwargs): pass
        def heartbeat(self,*args): return True
        def recover_stale(self): return 0
    class Lease:
        acquired=True
        def __enter__(self): return self
        def __exit__(self,*args): pass
    barrier=threading.Barrier(2); moments=[]
    def runner(job,config):
        moments.append((job.id,"start",time.monotonic())); barrier.wait(timeout=2); time.sleep(.02)
        moments.append((job.id,"end",time.monotonic())); return {"status":"succeeded"}
    registry=AdapterRegistry(); registry.register("fake",runner)
    orchestrator=Orchestrator(None,queue=Queue(),registry=registry,max_parallel_supplier_jobs=2,lease_factory=lambda d:Lease())
    assert orchestrator.tick()==2
    wait(orchestrator.active,timeout=3)
    starts=[m[2] for m in moments if m[1]=="start"]; ends=[m[2] for m in moments if m[1]=="end"]
    assert max(starts) < min(ends)


def test_retry_after_backoff_and_metrics_without_wall_clock_sleep():
    clock = FakeClock(); metrics = RequestMetrics()
    policy = DomainRatePolicy(min_request_interval_seconds=0, jitter_max_seconds=0, max_attempts=3, backoff_base_seconds=.5)
    limiter = DomainRateLimiter(policy, clock=clock)
    responses = iter([Response(429, {"Retry-After": "2"}), Response(503), Response(200)])
    response, attempts = request_with_resilience(lambda: next(responses), policy, limiter, clock=clock, metrics=metrics)
    assert response.status_code == 200 and attempts == 3
    assert clock.sleeps == [2, 1.0]
    assert metrics.retry_count == 2 and metrics.http_429 == 1 and metrics.http_5xx == 1
    assert retry_after_seconds({"Retry-After": "3"}) == 3


def test_circuit_breaker_opens_and_recovers_after_cooldown():
    clock = FakeClock(); metrics = RequestMetrics()
    policy = DomainRatePolicy(consecutive_error_threshold=2, cooldown_seconds=4)
    breaker = CircuitBreaker(policy, clock=clock, metrics=metrics)
    breaker.failure("x"); breaker.failure("x")
    with pytest.raises(CircuitOpen): breaker.before_request()
    clock.sleep(4); breaker.before_request(); breaker.success()
    assert metrics.circuit_breaker_trips == 1


def test_timezone_validation_and_exactly_three_future_runs():
    base = datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc)
    values = next_occurrences("0 2 * * *", "Europe/Berlin", after=base)
    assert len(values) == 3 and all(value.tzinfo == timezone.utc for value in values)
    with pytest.raises(ValueError): validate_schedule("not cron", "UTC")


def test_registry_is_explicit_and_rejects_duplicates():
    registry = AdapterRegistry(); fn = lambda job, config: {}
    registry.register("fake", fn)
    assert registry.resolve("fake") is fn
    with pytest.raises(ValueError): registry.register("fake", fn)


def test_web_pages_filters_banner_health_and_enqueue_only():
    store = SnapshotControlPlaneStore(ROOT / "reports"); queue = InMemoryJobQueue()
    client = TestClient(create_app(store=store, queue=queue))
    for path in ("/", "/products?page_size=25", "/review", "/suppliers", "/runs", "/scheduler"):
        response = client.get(path)
        assert response.status_code == 200
        assert "STERBRUST IMPORT DISABLED" in response.text
    filtered = client.get("/products?supplier=optimum&page_size=25")
    assert "Optimum" in filtered.text and "server-side filtering" in filtered.text
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["postgresql"] == "demo_snapshot"
    response = client.post("/suppliers/1/start", data={"crawl_type": "full"}, follow_redirects=False)
    assert response.status_code == 303 and queue.jobs[0]["status"] == "queued"
    assert not any(token in route.path.lower() for route in client.app.routes for token in ("sterbrust/write", "import", "product/create"))


def test_all_product_filters_searches_pagination_and_stable_sorting():
    store=SnapshotControlPlaneStore(ROOT / "reports")
    combined=store.products(ProductFilters(supplier="optimum",brand="Optimum",decision="ACCESSORY_OR_COMPATIBILITY_ITEM",category="патроны",page_size=25))
    assert combined["total"] > 0
    assert all(r["supplier_code"]=="optimum" and r["brand"]=="Optimum" and r["decision"]=="ACCESSORY_OR_COMPATIBILITY_ITEM" for r in combined["items"])
    exemplar=combined["items"][0]
    for value in (exemplar["external_id"],exemplar["SKU"],exemplar["own_model"]):
        assert store.products(ProductFilters(search=value,page_size=25))["total"] > 0
    sterbrust=next((r for r in store._rows if r.get("best_sterbrust_id")),None)
    if sterbrust: assert store.products(ProductFilters(search=sterbrust["best_sterbrust_id"],page_size=25))["total"] > 0
    page1=store.products(ProductFilters(page=1,page_size=25))["items"]
    page2=store.products(ProductFilters(page=2,page_size=25))["items"]
    assert len(page1)==25 and len(page2)==25 and page1 != page2
    assert page1 == store.products(ProductFilters(page=1,page_size=25))["items"]
    where,params=product_where(ProductFilters(availability="in_stock",content="complete",media="missing",page_size=25))
    assert "availability_normalized" in where and "description_text" in where and "image_count=0" in where and params==["in_stock"]
