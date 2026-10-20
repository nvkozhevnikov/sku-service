from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
import threading
import time
from typing import Callable

from universal_supplier.postgres import PostgresConfig
from .adapter_registry import AdapterRegistry, default_adapter_registry
from .heartbeat import HeartbeatRecorder
from .queue import CrawlJob, PostgresDomainLease, PostgresJobQueue

LOGGER = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config: PostgresConfig, *, queue: PostgresJobQueue | None = None,
                 registry: AdapterRegistry | None = None, max_parallel_supplier_jobs: int = 2,
                 poll_seconds: float = 2.0, lease_factory: Callable | None = None, heartbeat=None) -> None:
        if max_parallel_supplier_jobs < 1:
            raise ValueError("max_parallel_supplier_jobs must be positive")
        self.config = config
        self.queue = queue or PostgresJobQueue(config)
        self.registry = registry or default_adapter_registry()
        self.max_parallel = max_parallel_supplier_jobs
        self.poll_seconds = poll_seconds
        self.lease_factory = lease_factory or (lambda domain: PostgresDomainLease(config, domain))
        self.pool = ThreadPoolExecutor(max_workers=max_parallel_supplier_jobs, thread_name_prefix="supplier-job")
        self.active: set[Future] = set()
        self.recover_interval_seconds = 60.0; self._last_recover: float | None = None
        self.heartbeat = heartbeat

    def recover(self) -> int:
        self._last_recover = time.monotonic()
        return self.queue.recover_stale()

    def _run_job(self, job: CrawlJob) -> None:
        if not self.registry.supports(job.adapter_name, job.crawl_type):
            self.queue.complete(
                job.id, "failed",
                error_message=f"UNSUPPORTED_CRAWL_TYPE: {job.adapter_name} does not support {job.crawl_type}",
            )
            return
        with self.lease_factory(job.domain) as lease:
            if not lease.acquired:
                self.queue.complete(job.id, "skipped_locked", error_message="SAME_DOMAIN_LOCK_NOT_ACQUIRED")
                return
            stop = threading.Event()

            def heartbeat() -> None:
                while not stop.wait(max(1.0, self.queue.lease_seconds / 3)):
                    if not self.queue.heartbeat(job.id):
                        return

            heart = threading.Thread(target=heartbeat, daemon=True)
            heart.start()
            try:
                runner = self.registry.resolve(job.adapter_name)
                result = runner(job, self.config)
            except Exception as error:
                self._complete(job, "failed", error_message=f"{type(error).__name__}: {str(error)[:2000]}")
            else:
                self._complete(job, result.get("status", "succeeded"), crawl_run_id=result.get("crawl_run_id"))
            finally:
                stop.set()
                heart.join(timeout=1)

    def _complete(self, job: CrawlJob, status: str, **kwargs) -> None:
        try:
            self.queue.complete(job.id, status, **kwargs)
        except RuntimeError:
            # Usually the lease expired and recover_stale() already marked the job stale_failed;
            # keep that verdict but record the result we could not store.
            LOGGER.error("job %s finished as %s but could not be completed (already stale?): %s",
                         job.id, status, kwargs, exc_info=True)

    @staticmethod
    def _log_failure(job_id: int, future: Future) -> None:
        # _run_job handles runner errors itself; anything escaping it (lease,
        # queue.complete) would otherwise vanish silently with the future.
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            LOGGER.error("supplier job %s crashed outside the runner", job_id, exc_info=error)

    def tick(self) -> int:
        if self._last_recover is None or time.monotonic() - self._last_recover >= self.recover_interval_seconds:
            self.recover()
        self.active = {future for future in self.active if not future.done()}
        submitted = 0
        while len(self.active) < self.max_parallel:
            job = self.queue.claim_next()
            if job is None:
                break
            future = self.pool.submit(self._run_job, job)
            future.add_done_callback(lambda done, job_id=job.id: self._log_failure(job_id, done))
            self.active.add(future)
            submitted += 1
        return submitted

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as error:
                LOGGER.exception("orchestrator tick failed; retrying")
                if self.heartbeat: self.heartbeat.record(error)
            else:
                if self.heartbeat: self.heartbeat.record()
            time.sleep(self.poll_seconds)


def main() -> None:
    config = PostgresConfig.from_env()
    maximum = int(os.environ.get("MAX_PARALLEL_SUPPLIER_JOBS", "2"))
    orchestrator = Orchestrator(config, max_parallel_supplier_jobs=maximum)
    orchestrator.heartbeat = HeartbeatRecorder(config, "orchestrator", orchestrator.poll_seconds)
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
