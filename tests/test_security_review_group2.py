"""Security review 2026-10, group 2: SSRF on scheduled crawls, circuit breaker, crawler robustness."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import logging
from pathlib import Path
import threading
import xml.etree.ElementTree as ET

import httpx
import pytest

from universal_supplier.adapters.optimum_source import fetch as optimum_fetch
from universal_supplier.adapters.optimum_source.discovery import OptimumDiscovery
from universal_supplier.adapters.optimum_source.fetch import FetchResult, HttpFetcher
from universal_supplier.adapters.optimum_source.normalize import normalize_url
from universal_supplier.adapters.partner_st import PartnerStAdapter
from universal_supplier.commercial_collection import SITE_SEEDS, run_commercial_collection
from universal_supplier.commercial_discovery import fetch_catalog_page
from universal_supplier.control_plane.rate_control import DomainRatePolicy, RequestMetrics
from universal_supplier.crawl import run_partner_st_live
from universal_supplier.http import HttpTransport
from universal_supplier.http_capture import CaptureStatus, HttpResponse, ResponseTooLargeError
from universal_supplier.models import DiscoveryResult
from universal_supplier.stage6e import MissingState, NON_MISSING, observe_missing, validate_supplier_url
from universal_supplier.url_safety import UnsafeUrlError, host_validator, validate_public_http_url
from tests.test_stage6c1_optimum import FakeAsyncClient, FakeAsyncClock
from tests.test_stage6c1_partner import FakeClock, FakeResponse, FakeSession

INTERNAL_TARGETS = ("http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/")


def public_resolver(host, port):
    return [(None, None, None, "", ("93.184.216.34", port))]


def quiet_policy(**overrides) -> DomainRatePolicy:
    values = dict(max_concurrency=1, min_request_interval_seconds=0, jitter_min_seconds=0, jitter_max_seconds=0,
                  max_attempts=1, backoff_base_seconds=0, backoff_max_seconds=0)
    values.update(overrides)
    return DomainRatePolicy(**values)


# --- URL validator ------------------------------------------------------------

@pytest.mark.parametrize("url", [
    *INTERNAL_TARGETS, "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:10.0.0.5]/", "http://0.0.0.0/",
    "ftp://partner-st.ru/", "https://user:pw@partner-st.ru/", "https://partner-st.ru:8443/", "javascript:alert(1)",
])
def test_public_validator_refuses_internal_and_odd_targets(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)


def test_host_validator_checks_dns_answers():
    private = lambda host, port: [(None, None, None, "", ("10.1.2.3", port))]
    with pytest.raises(UnsafeUrlError):
        host_validator(["partner-st.ru"], resolver=private)("https://partner-st.ru/p")
    assert host_validator(["partner-st.ru"], resolver=public_resolver)("https://partner-st.ru:443/p")
    with pytest.raises(UnsafeUrlError):
        host_validator(["partner-st.ru"], resolver=public_resolver)("https://evil.example/p")


def test_supplier_validator_refuses_non_standard_ports():
    with pytest.raises(ValueError):
        validate_supplier_url("https://partner-st.ru:8080/x", ["partner-st.ru"])
    assert validate_supplier_url("https://partner-st.ru:443/x", ["partner-st.ru"])


# --- Partner-ST (requests) ----------------------------------------------------

def _redirect(location: str) -> FakeResponse:
    return FakeResponse(302, headers={"Location": location})


@pytest.mark.parametrize("target", INTERNAL_TARGETS)
def test_partner_transport_never_requests_internal_redirect_target(target):
    session = FakeSession([_redirect(target), FakeResponse()])
    validator = host_validator(PartnerStAdapter.allowed_hosts, resolver=public_resolver)
    transport = HttpTransport(session=session, domain_policy=quiet_policy(), clock=FakeClock(), url_validator=validator)
    with pytest.raises(UnsafeUrlError):
        transport.get("https://partner-st.ru/product/1")
    assert [url for url, _ in session.calls] == ["https://partner-st.ru/product/1"]
    assert all(kwargs["allow_redirects"] is False for _, kwargs in session.calls)


@pytest.mark.parametrize("target", INTERNAL_TARGETS)
def test_default_transport_also_refuses_private_redirects(target):
    session = FakeSession([_redirect(target), FakeResponse()])
    with pytest.raises(UnsafeUrlError):
        HttpTransport(session=session, domain_policy=quiet_policy(), clock=FakeClock()).get("https://example.test/a")
    assert len(session.calls) == 1


def test_partner_crawl_classifies_unsafe_redirect_as_unsafe_url():
    class Adapter:
        base_url = "https://partner-st.ru/"
        url_validator = staticmethod(host_validator(PartnerStAdapter.allowed_hosts, resolver=public_resolver))
        def discover(self, transport):
            return DiscoveryResult("https://partner-st.ru/sitemap.xml", (), (), ("https://partner-st.ru/product/1",))
        def parse_product(self, fetched):
            raise AssertionError("unsafe response must not be parsed")

    session = FakeSession([_redirect(INTERNAL_TARGETS[1]), FakeResponse()])
    result = run_partner_st_live(baseline=None, rate_policy=quiet_policy(), clock=FakeClock(),
                                 transport_factory=lambda **kwargs: HttpTransport(session=session, **kwargs),
                                 adapter=Adapter())
    assert [error.error_type for error in result.errors] == ["UNSAFE_URL"]
    assert len(session.calls) == 1


def test_partner_discovery_ignores_foreign_sitemap_locs():
    class Transport:
        def __init__(self, pages): self.pages = pages; self.calls = []
        def get(self, url):
            from universal_supplier.models import FetchRecord
            self.calls.append(url)
            return FetchRecord(url, url, 200, (), 1, 0, self.pages[url])

    transport = Transport({
        "https://partner-st.ru/sitemap.xml": "<sitemapindex><sitemap><loc>http://169.254.169.254/products_1.xml</loc></sitemap>"
                                             "<sitemap><loc>https://partner-st.ru/products_2.xml</loc></sitemap></sitemapindex>",
        "https://partner-st.ru/products_2.xml": "<urlset><url><loc>https://partner-st.ru/p1/</loc></url>"
                                                "<url><loc>http://10.0.0.5/p2/</loc></url></urlset>",
    })
    result = PartnerStAdapter().discover(transport)
    assert result.product_urls == ("https://partner-st.ru/p1/",)
    assert transport.calls == ["https://partner-st.ru/sitemap.xml", "https://partner-st.ru/products_2.xml"]


# --- Optimum (httpx) ----------------------------------------------------------

@pytest.mark.parametrize("target", INTERNAL_TARGETS + ("https://evil.example/catalog/",))
def test_optimum_fetcher_never_requests_unsafe_redirect_target(target):
    client = FakeAsyncClient([FakeResponseOptimum(302, {"location": target}), FakeResponseOptimum(200)])

    async def run():
        fetcher = HttpFetcher(rate_policy=quiet_policy(), clock=FakeAsyncClock(), client=client)
        try:
            await fetcher.get("https://optimum.su/catalog/a/")
        finally:
            await fetcher.close()

    with pytest.raises(UnsafeUrlError):
        asyncio.run(run())
    assert [url for url, _ in client.calls] == ["https://optimum.su/catalog/a/"]


class FakeResponseOptimum:
    def __init__(self, status_code=200, headers=None, text="ok"):
        self.status_code = status_code; self.headers = headers or {}; self.text = text
        self.url = "https://optimum.su/ignored/"


def test_optimum_follows_safe_redirect_and_reports_final_url():
    client = FakeAsyncClient([FakeResponseOptimum(301, {"location": "/catalog/b/"}), FakeResponseOptimum(200, text="B")])

    async def run():
        fetcher = HttpFetcher(rate_policy=quiet_policy(), clock=FakeAsyncClock(), client=client)
        try:
            return await fetcher.get("https://optimum.su/catalog/a/")
        finally:
            await fetcher.close()

    result = asyncio.run(run())
    assert (result.final_url, result.text, result.attempt_count) == ("https://optimum.su/catalog/b/", "B", 2)


def test_optimum_adapter_classifies_unsafe_redirect_as_unsafe_url():
    from universal_supplier.adapters.optimum_source.adapter import OptimumAdapter
    from universal_supplier.adapters.optimum_source.models import DiscoveryRecord
    client = FakeAsyncClient([FakeResponseOptimum(302, {"location": INTERNAL_TARGETS[0]})])
    adapter = OptimumAdapter(output_dir=Path("unused"), rate_policy=quiet_policy(), clock=FakeAsyncClock(), client=client)
    record = DiscoveryRecord(source_url="https://optimum.su/catalog/a/", canonical_url="https://optimum.su/catalog/a/",
                             discovery_source="sitemap", discovered_at="2026-10-20T00:00:00+00:00")
    row = asyncio.run(adapter._one(record))
    assert row["classification"] == "FETCH_ERROR" and row["error_type"] == "UNSAFE_URL"


def test_optimum_real_httpx_client_redirects_manually_and_caps_body(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/catalog/moved/":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        if request.url.path == "/catalog/big/":
            return httpx.Response(200, content=b"x" * 500)
        return httpx.Response(200, content="Привет".encode("utf-8"), headers={"content-type": "text/html; charset=utf-8"})

    monkeypatch.setattr(optimum_fetch, "MAX_RESPONSE_BYTES", 100)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fetcher = HttpFetcher(rate_policy=quiet_policy(), clock=FakeAsyncClock(), client=client)
        try:
            ok = await fetcher.get("https://optimum.su/catalog/ok/")
            with pytest.raises(UnsafeUrlError):
                await fetcher.get("https://optimum.su/catalog/moved/")
            with pytest.raises(ResponseTooLargeError):
                await fetcher.get("https://optimum.su/catalog/big/")
            return ok
        finally:
            await fetcher.close()

    ok = asyncio.run(run())
    assert ok.text == "Привет"
    assert "http://169.254.169.254/" not in seen
    assert seen.count("https://optimum.su/catalog/big/") == 1  # an oversized body is not retried


def test_optimum_normalize_url_drops_foreign_hosts_for_navigation():
    assert normalize_url("https://evil.example/catalog/x/") is None
    assert normalize_url("http://10.0.0.5/catalog/") is None
    assert normalize_url("https://optimum.su:8443/catalog/") is None
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("/catalog/x/") == "https://optimum.su/catalog/x/"
    assert normalize_url("https://youtube.com/embed/a", allow_foreign=True) == "https://youtube.com/embed/a"
    assert normalize_url("javascript:alert(1)", allow_foreign=True) is None


def test_optimum_sitemap_discovery_filters_by_host_not_only_path():
    pages = {
        "https://optimum.su/sitemap.xml": "<sitemapindex><sitemap><loc>http://169.254.169.254/sitemap-1.xml</loc></sitemap>"
                                          "<sitemap><loc>https://optimum.su/sitemap-2.xml</loc></sitemap></sitemapindex>",
        "https://optimum.su/sitemap-2.xml": "<urlset><url><loc>https://optimum.su/catalog/a/</loc></url>"
                                            "<url><loc>https://evil.example/catalog/b/</loc></url></urlset>",
    }

    class Fetcher:
        calls = []
        async def get(self, url):
            self.calls.append(url)
            return FetchResult(url, url, 200, pages[url])

    fetcher = Fetcher()
    maps, urls = asyncio.run(OptimumDiscovery(fetcher).sitemap_urls())
    assert maps == ["https://optimum.su/sitemap-2.xml"]
    assert urls == ["https://optimum.su/catalog/a/"]
    assert "http://169.254.169.254/sitemap-1.xml" not in fetcher.calls


# --- Commercial collection ------------------------------------------------------

def test_commercial_product_capture_refuses_redirect_off_the_source_host():
    first, second = SITE_SEEDS["beka_mak"]
    product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
    fixture = (Path(__file__).with_name("fixtures") / "commercial" / "real" / "bekamak_bms_230dg_sanitized.html").read_bytes()

    class Client:
        def __init__(self): self.calls = []
        def get_validated(self, url, *, timeout_seconds, validator):
            validator(url); self.calls.append(url)
            if "/product/" in url:
                # The server redirected to an internal host; the validator must stop it.
                validator(INTERNAL_TARGETS[1])
                return HttpResponse(INTERNAL_TARGETS[1], 200, "text/html", fixture)
            body = f"<a href='{product_url}'>BMS 230 DG</a>".encode() if url == first else b""
            return HttpResponse(url, 200, "text/html", body)

    from tests.test_commercial_collection import MemoryEvidenceStore
    result = run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0, evidence_dir=Path.cwd(),
                                       dry_run=True, client=Client(), sleep=lambda _: None,
                                       evidence_store=MemoryEvidenceStore())
    product_rows = [row for row in result.rows if row.phase == "product"]
    assert [row.result for row in product_rows] == ["UNSAFE_URL"]
    assert result.site_results[0].fetched_cards == 0


def test_catalog_fetch_classifies_validator_and_http_errors():
    url = SITE_SEEDS["beka_mak"][0]

    class Raising:
        def __init__(self, error): self.error = error
        def get_validated(self, url, *, timeout_seconds, validator): raise self.error

    unsafe = fetch_catalog_page("beka_mak", url, evidence_dir=Path.cwd(), client=Raising(ValueError("redirect off host")))
    assert unsafe.status is CaptureStatus.UNSAFE_URL
    broken = fetch_catalog_page("beka_mak", url, evidence_dir=Path.cwd(), client=Raising(http.client.IncompleteRead(b"")))
    assert broken.status is CaptureStatus.NETWORK_ERROR


# --- Circuit breaker, metrics, missing evidence ----------------------------------

def test_request_metrics_are_thread_safe():
    metrics = RequestMetrics()

    def work():
        for _ in range(2000):
            metrics.record_status(200, 0.0); metrics.add(retry_count=1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    data = metrics.as_metadata()
    assert data["requests_total"] == data["http_2xx"] == data["retry_count"] == 16000
    assert "_lock" not in data


@pytest.mark.parametrize("error_type", ["CircuitOpen", "CIRCUIT_OPEN", "MaxRunDurationExceeded",
                                        "MAX_RUN_DURATION_EXCEEDED", "UNSAFE_URL"])
def test_breaker_deadline_and_unsafe_errors_are_not_missing_evidence(error_type):
    previous = MissingState(2, "WARNING", "NOT_DISCOVERED")
    assert observe_missing(previous, error_type) .consecutive_confirmed == 2
    assert {"CIRCUIT_OPEN", "MAX_RUN_DURATION_EXCEEDED"} <= NON_MISSING


# --- Scheduler, orchestrator, XML --------------------------------------------------

class _Cursor:
    def __init__(self, rows): self.rows = rows; self.updates = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None):
        if sql.lstrip().startswith("UPDATE"): self.updates.append(params[-1])
    def fetchall(self): return self.rows


class _Connection:
    def __init__(self, cursor): self._cursor = cursor; self.savepoints = 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    @contextmanager
    def transaction(self):
        self.savepoints += 1
        yield
    def cursor(self): return self._cursor


def test_scheduler_skips_one_broken_schedule_row_and_keeps_the_tick(caplog):
    from universal_supplier.control_plane.queue import EnqueueResult
    from universal_supplier.control_plane.scheduler import Scheduler

    now = datetime(2026, 10, 20, 2, 0, 5, tzinfo=timezone.utc)
    cursor = _Cursor([(1, "not a cron", "UTC", "full", None), (2, "0 2 * * *", "UTC", "full", None)])

    class Queue:
        def enqueue(self, supplier_id, **kwargs): return EnqueueResult(supplier_id * 10, "queued", None)

    scheduler = Scheduler(None, Queue()); scheduler._connect = lambda: _Connection(cursor)
    with caplog.at_level(logging.ERROR):
        assert scheduler.tick(now) == [20]
    assert cursor.updates == [2]
    assert "supplier 1 skipped" in caplog.text


def test_orchestrator_logs_job_crash_outside_runner(caplog):
    from universal_supplier.control_plane.orchestrator import Orchestrator
    from universal_supplier.control_plane.queue import CrawlJob

    class Registry:
        def supports(self, *args): return True

    class Queue:
        def __init__(self):
            self.jobs = [CrawlJob(7, 1, "fake", "fake", "https://example.test/", "manual", "full", "running",
                                  datetime(2026, 10, 20, tzinfo=timezone.utc), {})]
        def recover_stale(self): return 0
        def claim_next(self): return self.jobs.pop() if self.jobs else None

    def broken_lease(domain): raise RuntimeError("lease table missing")

    orchestrator = Orchestrator(None, queue=Queue(), registry=Registry(), lease_factory=broken_lease)
    with caplog.at_level(logging.ERROR):
        orchestrator.tick(); orchestrator.pool.shutdown(wait=True)
    assert "supplier job 7 crashed" in caplog.text and "lease table missing" in caplog.text


def test_full_xml_supplier_code_attribute_is_quoted_safely():
    from universal_supplier.xml_export import iter_full_xml
    body = b"".join(iter_full_xml([{"supplier_code": 'a" evil="1', "source_product_id": "1", "external_id": "1"}],
                                  details_for=lambda ids: {}))
    root = ET.fromstring(body)
    supplier = root.find(".//supplier")
    assert supplier.get("code") == 'a" evil="1' and supplier.get("evil") is None
