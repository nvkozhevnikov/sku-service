"""Persisted, truthful progress for live supplier crawls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Callable


PHASE_LABELS_RU = {
    "QUEUED": "В очереди",
    "DISCOVERY": "Поиск товаров",
    "FETCHING_PRODUCTS": "Загрузка товаров",
    "INGESTING": "Сохранение данных",
    "MATCHING": "Сопоставление",
    "REVIEW_SYNC": "Синхронизация проверки",
    "FINISHED": "Завершено",
}


def format_eta_ru(seconds: int | float | None) -> str:
    if seconds is None:
        return "Время окончания пока неизвестно"
    value = max(0, round(seconds))
    if value < 60:
        return f"Осталось примерно {value} сек"
    if value < 3600:
        minutes, remainder = divmod(value, 60)
        return f"Осталось примерно {minutes} мин {remainder} сек"
    hours, remainder = divmod(value, 3600)
    minutes = remainder // 60
    return f"Осталось примерно {hours} ч {minutes:02d} мин"


@dataclass(frozen=True)
class ProgressClock:
    monotonic: Callable[[], float] = monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class CrawlProgressTracker:
    """Thread-safe tracker; persistence is throttled but phase/final writes are forced."""

    def __init__(self, persist: Callable[[dict[str, Any]], None], *, clock: ProgressClock | None = None,
                 persist_interval: float = 1.0, persist_every: int = 25) -> None:
        self._persist = persist
        self._clock = clock or ProgressClock()
        self._interval = persist_interval
        self._every = persist_every
        self._lock = Lock()
        self._started = self._clock.monotonic()
        self._fetch_started: float | None = None
        self._fetch_processed_base = 0
        self._last_persisted = self._started
        self._last_persisted_processed = 0
        self._phase = "QUEUED"
        self._total: int | None = None
        self._processed = self._succeeded = self._errors = 0
        self._status = "running"

    def _snapshot_locked(self) -> dict[str, Any]:
        elapsed = max(0.0, self._clock.monotonic() - self._started)
        fetch_elapsed = None if self._fetch_started is None else max(0.0, self._clock.monotonic() - self._fetch_started)
        fetch_processed = self._processed - self._fetch_processed_base
        rate = fetch_processed / fetch_elapsed if fetch_processed > 0 and fetch_elapsed and fetch_elapsed > 0 else None
        percent = None if self._total is None else (100.0 if self._total == 0 else min(100.0, self._processed * 100.0 / self._total))
        eta = None
        if self._phase == "FETCHING_PRODUCTS" and rate and self._total is not None and self._processed < self._total:
            eta = max(0.0, (self._total - self._processed) / rate)
        eta_seconds = None if eta is None else round(eta)
        return {
            "phase": self._phase, "phase_label_ru": PHASE_LABELS_RU[self._phase],
            "status": self._status, "total": self._total, "processed": self._processed,
            "succeeded": self._succeeded, "errors": self._errors,
            "percent": None if percent is None else round(percent, 2),
            "rate_per_second": None if rate is None else round(rate, 3),
            "eta_seconds": eta_seconds,
            "eta_human_ru": format_eta_ru(eta_seconds),
            "elapsed_seconds": round(elapsed, 3),
            "fetch_elapsed_seconds": None if fetch_elapsed is None else round(fetch_elapsed, 3),
            "updated_at": self._clock.now().isoformat(),
        }

    def _maybe_persist_locked(self, force: bool = False) -> dict[str, Any]:
        now = self._clock.monotonic()
        due = (now - self._last_persisted >= self._interval or
               self._processed - self._last_persisted_processed >= self._every)
        snapshot = self._snapshot_locked()
        if force or due:
            self._persist(snapshot)
            self._last_persisted = now
            self._last_persisted_processed = self._processed
        return snapshot

    def set_phase(self, phase: str, *, total: int | None = None) -> dict[str, Any]:
        if phase not in PHASE_LABELS_RU:
            raise ValueError(f"unknown crawl phase: {phase}")
        if total is not None and total < 0:
            raise ValueError("progress total cannot be negative")
        with self._lock:
            if phase == "FETCHING_PRODUCTS" and self._fetch_started is None:
                self._fetch_started = self._clock.monotonic()
                self._fetch_processed_base = self._processed
            self._phase = phase
            if total is not None:
                self._total = total
            return self._maybe_persist_locked(force=True)

    def complete(self, *, success: bool) -> dict[str, Any]:
        with self._lock:
            self._processed += 1
            self._succeeded += int(success)
            self._errors += int(not success)
            return self._maybe_persist_locked()

    def finish(self, status: str) -> dict[str, Any]:
        with self._lock:
            self._phase = "FINISHED"
            self._status = status
            return self._maybe_persist_locked(force=True)

    def callback(self, event: str, **values: Any) -> dict[str, Any]:
        if event == "DISCOVERY":
            return self.set_phase("DISCOVERY")
        if event == "FETCHING_PRODUCTS":
            return self.set_phase("FETCHING_PRODUCTS", total=int(values["total"]))
        if event == "PRODUCT_COMPLETED":
            return self.complete(success=bool(values.get("success")))
        return self.set_phase(event, total=values.get("total"))
