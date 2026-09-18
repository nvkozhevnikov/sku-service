from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
from typing import Any, Callable

from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.crawl import run_partner_st_live
from universal_supplier.adapters.optimum import run_optimum_live_sync
from universal_supplier.state import discovery_health
from universal_supplier.identity_service import reconcile_runtime_identity

from .queue import CrawlJob
from .progress import CrawlProgressTracker
from .rate_control import (
    DomainRatePolicy,
    OPTIMUM_DEFAULT_RATE_POLICY,
    PARTNER_ST_DEFAULT_RATE_POLICY,
)


Runner = Callable[[CrawlJob, PostgresConfig], dict[str, Any]]


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_name: str
    runner: Runner
    supported_crawl_types: frozenset[str]


class AdapterRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, AdapterRegistration] = {}

    def register(self, adapter_name: str, runner: Runner, *,
                 supported_crawl_types: set[str] | frozenset[str] = frozenset({"full"})) -> None:
        if not adapter_name or adapter_name in self._registrations:
            raise ValueError(f"duplicate/empty adapter registration: {adapter_name}")
        supported = frozenset(supported_crawl_types)
        if not supported:
            raise ValueError(f"adapter has no supported crawl types: {adapter_name}")
        self._registrations[adapter_name] = AdapterRegistration(adapter_name, runner, supported)

    def resolve(self, adapter_name: str) -> Runner:
        try:
            return self._registrations[adapter_name].runner
        except KeyError as error:
            raise KeyError(f"unregistered supplier adapter: {adapter_name}") from error

    def supports(self, adapter_name: str, crawl_type: str) -> bool:
        try:
            return crawl_type in self._registrations[adapter_name].supported_crawl_types
        except KeyError as error:
            raise KeyError(f"unregistered supplier adapter: {adapter_name}") from error

    def capabilities(self, adapter_name: str) -> AdapterRegistration:
        try:
            return self._registrations[adapter_name]
        except KeyError as error:
            raise KeyError(f"unregistered supplier adapter: {adapter_name}") from error

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))


def _finish_metadata(repository: PostgresRepository, run_id: int, metadata: dict) -> None:
    force_partial = (metadata.get("identity_post_processing") or {}).get("status") == "POST_PROCESSING_FAILED"
    payload = {key: value for key, value in metadata.items() if key != "deactivation_suppressed"}
    with repository.connection.transaction(), repository.connection.cursor() as cursor:
        cursor.execute(
            """UPDATE crawl_runs SET
                   status=CASE WHEN %s THEN 'partial' ELSE status END,
                   error_count=(SELECT count(*) FROM crawl_errors WHERE crawl_run_id=%s),
                   metadata=metadata || %s::jsonb
               WHERE id=%s""",
            (force_partial, run_id, __import__("json").dumps(payload, ensure_ascii=False), run_id),
        )


def _adapter_result(repository: PostgresRepository, run_id: int, counts) -> dict[str, Any]:
    """Job/orchestrator status is the persisted crawl_runs status, never a parallel guess."""
    return {
        "status": repository.final_crawl_run_status(run_id),
        "crawl_run_id": run_id,
        "counts": counts.__dict__,
    }


def _policy_from_snapshot(
    settings: dict[str, Any] | None,
    default: DomainRatePolicy,
) -> DomainRatePolicy:
    """Overlay a persisted partial snapshot on the supplier's real baseline."""
    values = default.snapshot()
    values.update(settings or {})
    return DomainRatePolicy.from_mapping(values)


def _post_ingest_identity(config: PostgresConfig, supplier_code: str) -> dict[str, Any]:
    """The only normal runtime path from successful ingest to review state."""
    if config is None:  # explicit unit-test adapter doubles use no database config
        return {"skipped": "NO_DATABASE_CONFIG"}
    return {"status": "succeeded", **reconcile_runtime_identity(config, supplier_code)}


def _tracker(repository, run_id: int) -> CrawlProgressTracker:
    persist = getattr(repository, "update_crawl_progress", None)
    return CrawlProgressTracker(
        (lambda snapshot: persist(run_id, snapshot)) if persist else (lambda snapshot: None)
    )


def _run_with_progress(runner, callback, **kwargs):
    """Keep existing adapter doubles compatible while wiring real runners."""
    try:
        supports = "progress_callback" in inspect.signature(runner).parameters
    except (TypeError, ValueError):
        supports = False
    if supports:
        kwargs["progress_callback"] = callback
    return runner(**kwargs)


def _schema_signals(cards, errors) -> list[str]:
    """Generate parser-structure evidence from a normal crawl, never caller input."""
    signals: set[str] = set()
    error_types = {str(getattr(error, "error_type", "") or (error.get("error_type") if isinstance(error, dict) else "")) for error in errors}
    if "PARSER_ERROR" in error_types:
        signals.add("parser_error")
    for card in cards:
        raw_signals = (getattr(card, "raw_data", None) or {}).get("schema_signals", ())
        if isinstance(raw_signals, (list, tuple, set)):
            signals.update(str(value) for value in raw_signals if value)
    return sorted(signals)


def run_partner_st_job(job: CrawlJob, config: PostgresConfig) -> dict[str, Any]:
    rate = _policy_from_snapshot(
        job.settings_snapshot.get("rate_settings"), PARTNER_ST_DEFAULT_RATE_POLICY,
    )
    crawl = job.settings_snapshot.get("crawl_settings") or {}
    baseline = crawl.get("baseline_discovered_count", 1222)
    repository = PostgresRepository(config)
    run_id = repository.start_crawl_run(
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        baseline_discovered_count=baseline,
        metadata={"job_id": job.id, "trigger_type": job.trigger_type, "settings_snapshot": job.settings_snapshot},
        supplier_code=job.supplier_code,
    )
    progress = _tracker(repository, run_id)
    try:
        result = _run_with_progress(run_partner_st_live, progress.callback, baseline=baseline, rate_policy=rate)
        for error in result.errors:
            repository.record_crawl_error(run_id, url=error.url, error_type=error.error_type,
                                          message=error.message, http_status=error.http_status)
        deadline_exceeded = result.termination_reason == "MAX_RUN_DURATION_EXCEEDED"
        health = replace(result.health, deactivation_allowed=False) if deadline_exceeded else result.health
        progress.set_phase("INGESTING")
        counts = repository.ingest(
            list(result.cards), result.finished_at, crawl_run_id=run_id,
            discovery_health=health, discovered_urls=list(result.discovered_urls),
            error_count=len(result.errors), supplier_code=job.supplier_code,
            schema_signals=_schema_signals(result.cards, result.errors),
            crawl_settings=crawl,
        )
        post_error = None
        try:
            progress.set_phase("MATCHING")
            identity = _post_ingest_identity(config, job.supplier_code)
            progress.set_phase("REVIEW_SYNC")
        except Exception as error:
            post_error = f"{type(error).__name__}: {str(error)[:1800]}"
            identity = {"status": "POST_PROCESSING_FAILED", "error": post_error}
            repository.record_crawl_error(run_id, url=job.base_url, error_type="POST_PROCESSING_FAILED", message=post_error)
        effective = result.effective_rate_policy
        _finish_metadata(repository, run_id, {
            "requested_rate_policy": rate.snapshot(), "effective_rate_policy": effective,
            "requested_effective_policy_match": rate.snapshot() == effective,
            "request_metrics": result.request_metrics,
            "discovery": result.discovery_metadata,
            "termination_reason": result.termination_reason,
            "max_run_duration_exceeded": deadline_exceeded,
            "identity_post_processing": identity,
        })
        progress.finish(repository.final_crawl_run_status(run_id))
        return _adapter_result(repository, run_id, counts)
    except Exception as error:
        repository.fail_crawl_run(run_id, finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), message=str(error))
        progress.finish("failed")
        raise
    finally:
        repository.close()


def run_optimum_job(job: CrawlJob, config: PostgresConfig) -> dict[str, Any]:
    rate = _policy_from_snapshot(
        job.settings_snapshot.get("rate_settings"), OPTIMUM_DEFAULT_RATE_POLICY,
    )
    crawl = job.settings_snapshot.get("crawl_settings") or {}
    baseline = crawl.get("baseline_discovered_count", 1351)
    repository = PostgresRepository(config)
    run_id = repository.start_crawl_run(
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        baseline_discovered_count=baseline,
        metadata={"job_id": job.id, "trigger_type": job.trigger_type, "settings_snapshot": job.settings_snapshot},
        supplier_code=job.supplier_code,
    )
    progress = _tracker(repository, run_id)
    try:
        result = _run_with_progress(run_optimum_live_sync, progress.callback, rate_policy=rate)
        health = discovery_health(len(result.cards), baseline)
        errors = [row for row in result.rows if row.get("classification") == "FETCH_ERROR"]
        for error in errors:
            repository.record_crawl_error(run_id, url=error.get("source_url") or job.base_url,
                                          error_type=error.get("error_type") or "FETCH_ERROR",
                                          message=error.get("error") or "fetch failed",
                                          http_status=error.get("http_status") or None)
        termination_reason = result.metadata.get("termination_reason")
        deadline_exceeded = bool(
            result.metadata.get("max_run_duration_exceeded")
            or result.metadata.get("deadline_exceeded")
            or termination_reason == "MAX_RUN_DURATION_EXCEEDED"
        )
        if deadline_exceeded:
            termination_reason = "MAX_RUN_DURATION_EXCEEDED"
            repository.record_crawl_error(
                run_id,
                url=job.base_url,
                error_type=termination_reason,
                message=(
                    f"monotonic crawl deadline of {rate.max_run_duration_seconds:g}s "
                    "was exceeded; pending work was cancelled"
                ),
            )
            health = replace(health, deactivation_allowed=False)
        progress.set_phase("INGESTING")
        counts = repository.ingest(
            list(result.cards), result.finished_at, crawl_run_id=run_id,
            discovery_health=health,
            discovered_urls=[str(row.get("source_url") or "") for row in result.rows],
            error_count=len(errors) + int(deadline_exceeded), supplier_code=job.supplier_code,
            schema_signals=_schema_signals(result.cards, errors),
            crawl_settings=crawl,
        )
        post_error = None
        try:
            progress.set_phase("MATCHING")
            identity = _post_ingest_identity(config, job.supplier_code)
            progress.set_phase("REVIEW_SYNC")
        except Exception as error:
            post_error = f"{type(error).__name__}: {str(error)[:1800]}"
            identity = {"status": "POST_PROCESSING_FAILED", "error": post_error}
            repository.record_crawl_error(run_id, url=job.base_url, error_type="POST_PROCESSING_FAILED", message=post_error)
        effective = result.metadata.get("effective_rate_policy")
        _finish_metadata(repository, run_id, {
            "requested_rate_policy": rate.snapshot(),
            "effective_rate_policy": effective,
            "requested_effective_policy_match": rate.snapshot() == effective,
            "termination_reason": termination_reason,
            "max_run_duration_exceeded": deadline_exceeded,
            "identity_post_processing": identity,
            **{key: value for key, value in result.metadata.items() if key != "deactivation_suppressed"},
        })
        progress.finish(repository.final_crawl_run_status(run_id))
        return _adapter_result(repository, run_id, counts)
    except Exception as error:
        repository.fail_crawl_run(run_id, finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), message=str(error))
        progress.finish("failed")
        raise
    finally:
        repository.close()


def default_adapter_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("partner_st", run_partner_st_job, supported_crawl_types={"full"})
    registry.register("optimum", run_optimum_job, supported_crawl_types={"full"})
    return registry
