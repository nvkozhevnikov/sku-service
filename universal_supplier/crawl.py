from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from .adapters.partner_st import PartnerStAdapter
from .control_plane.rate_control import (
    Clock, DomainRatePolicy, MaxRunDurationExceeded,
    PARTNER_ST_DEFAULT_RATE_POLICY, SystemClock,
)
from .http import HttpTransport
from .models import ProductCard
from .state import DiscoveryHealth, discovery_health


@dataclass(frozen=True)
class CrawlFailure:
    url: str
    error_type: str
    message: str
    http_status: int | None = None


@dataclass(frozen=True)
class LiveCrawlResult:
    started_at: str
    finished_at: str
    discovered_urls: tuple[str, ...]
    cards: tuple[ProductCard, ...]
    rows: tuple[dict[str, Any], ...]
    errors: tuple[CrawlFailure, ...]
    health: DiscoveryHealth
    discovery_metadata: dict[str, Any]
    request_metrics: dict[str, Any] = field(default_factory=dict)
    requested_rate_policy: dict[str, Any] = field(default_factory=dict)
    effective_rate_policy: dict[str, Any] = field(default_factory=dict)
    termination_reason: str | None = None


def run_partner_st_live(*, baseline: int | None, workers: int = 4,
                        interval: float = 0.20,
                        rate_policy: DomainRatePolicy | None = None,
                        clock: Clock | None = None,
                        random_uniform: Callable[[float, float], float] | None = None,
                        transport_factory: Callable[..., HttpTransport] | None = None,
                        adapter: PartnerStAdapter | None = None,
                        progress_callback: Callable[..., Any] | None = None) -> LiveCrawlResult:
    """One real dynamic-sitemap, direct-HTTP Partner-ST full crawl.

    ``rate_policy`` is the authoritative immutable job snapshot.  The legacy
    ``workers``/``interval`` arguments remain available for older Stage 3B
    scripts, but they are never allowed to replace fields of a supplied policy.
    """
    effective_policy = rate_policy or replace(
        PARTNER_ST_DEFAULT_RATE_POLICY,
        max_concurrency=workers,
        min_request_interval_seconds=interval,
    )
    runtime_clock = clock or SystemClock()
    create_transport = transport_factory or HttpTransport
    adapter = adapter or PartnerStAdapter()
    started = datetime.now(timezone.utc).isoformat()
    started_monotonic = runtime_clock.monotonic()
    deadline = started_monotonic + effective_policy.max_run_duration_seconds
    # Pass the exact policy instance to the actual transport.  HttpTransport
    # uses it for timeout, attempts, bounded backoff, Retry-After handling,
    # circuit breaking, jitter and the shared per-domain start gate.
    transport = create_transport(
        domain_policy=effective_policy,
        clock=runtime_clock,
        random_uniform=random_uniform,
        deadline=deadline,
    )
    if progress_callback:
        progress_callback("DISCOVERY")
    discovery = adapter.discover(transport)
    if progress_callback:
        progress_callback("FETCHING_PRODUCTS", total=len(discovery.product_urls))
    health = discovery_health(len(discovery.product_urls), baseline)
    rows: list[dict[str, Any]] = []
    cards: list[ProductCard] = []
    failures: list[CrawlFailure] = []

    def deadline_expired() -> bool:
        return runtime_clock.monotonic() >= deadline

    def process(item: tuple[int, str]):
        position, url = item
        # A future can be queued just before the deadline and start after it.
        # Refuse the HTTP call in that case as well.
        if deadline_expired():
            return position, None, None, None, True
        try:
            fetched = transport.get(url)
            if fetched.status_code != 200:
                failure = CrawlFailure(url, f"HTTP_{fetched.status_code}",
                                       f"HTTP status {fetched.status_code}", fetched.status_code)
                return position, None, failure, fetched, False
            return position, adapter.parse_product(fetched), None, fetched, False
        except MaxRunDurationExceeded:
            return position, None, None, None, True
        except Exception as error:
            failure = CrawlFailure(url, type(error).__name__, str(error)[:1000], None)
            return position, None, failure, None, False

    items = list(enumerate(discovery.product_urls, 1))
    cursor = 0
    deadline_seen = deadline_expired()
    skipped_after_deadline = 0
    cancelled_after_deadline = 0
    with ThreadPoolExecutor(max_workers=effective_policy.max_concurrency) as pool:
        pending: dict[Future, tuple[int, str]] = {}

        def submit_available() -> None:
            nonlocal cursor, deadline_seen
            while cursor < len(items) and len(pending) < effective_policy.max_concurrency:
                if deadline_expired():
                    deadline_seen = True
                    return
                item = items[cursor]
                cursor += 1
                pending[pool.submit(process, item)] = item

        submit_available()
        while pending:
            completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                position, card, failure, fetched, skipped = future.result()
                if skipped:
                    skipped_after_deadline += 1
                    deadline_seen = True
                    continue
                if progress_callback:
                    progress_callback("PRODUCT_COMPLETED", success=card is not None)
                url = discovery.product_urls[position - 1]
                if failure:
                    failures.append(failure)
                if card:
                    cards.append(card)
                rows.append({
                    "position": position, "requested_url": url,
                    "final_url": fetched.final_url if fetched else "",
                    "http_status": fetched.status_code if fetched else "",
                    "redirect_count": len(fetched.redirects) if fetched else 0,
                    "attempt_count": fetched.attempts if fetched else effective_policy.max_attempts,
                    "external_id": card.external_id if card else "",
                    "sku": card.sku if card else "", "name": card.name if card else "",
                    "result": "FETCHED" if card else "ERROR",
                    "error": "" if not failure else f"{failure.error_type}: {failure.message}",
                })

            if deadline_expired():
                deadline_seen = True
                for future in tuple(pending):
                    if future.cancel():
                        pending.pop(future)
                        cancelled_after_deadline += 1
            if not deadline_seen:
                submit_available()

    unscheduled = len(items) - cursor
    termination_reason = "MAX_RUN_DURATION_EXCEEDED" if deadline_seen else None
    if termination_reason:
        failures.append(CrawlFailure(
            getattr(adapter, "base_url", "https://partner-st.ru/"),
            termination_reason,
            (
                f"monotonic crawl deadline of {effective_policy.max_run_duration_seconds:g}s "
                f"was exceeded; unscheduled={unscheduled}, "
                f"cancelled={cancelled_after_deadline}, skipped={skipped_after_deadline}"
            ),
            None,
        ))
    rows.sort(key=lambda row: int(row["position"]))
    order = {url: index for index, url in enumerate(discovery.product_urls)}
    cards.sort(key=lambda card: order.get(card.requested_url, 10**9))
    request_metrics = (
        transport.metrics.as_metadata()
        if hasattr(getattr(transport, "metrics", None), "as_metadata") else {}
    )
    policy_snapshot = effective_policy.snapshot()
    return LiveCrawlResult(
        started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(),
        discovered_urls=discovery.product_urls,
        cards=tuple(cards),
        rows=tuple(rows),
        errors=tuple(failures),
        health=health,
        discovery_metadata={
            "sitemap_index": discovery.sitemap_index_url,
            "sitemaps": list(discovery.sitemap_urls),
            "product_sitemaps": list(discovery.product_sitemap_urls),
            "max_run_duration_exceeded": deadline_seen,
            "unscheduled_product_count": unscheduled,
            "cancelled_product_count": cancelled_after_deadline,
            "skipped_product_count": skipped_after_deadline,
        },
        request_metrics=request_metrics,
        requested_rate_policy=policy_snapshot,
        effective_rate_policy=policy_snapshot,
        termination_reason=termination_reason,
    )
