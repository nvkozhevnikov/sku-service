from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import os
import threading
import time
from typing import Callable

from universal_supplier.postgres import PostgresConfig
from .adapter_registry import AdapterRegistry, default_adapter_registry
from .queue import CrawlJob, PostgresDomainLease, PostgresJobQueue


class Orchestrator:
    def __init__(self, config: PostgresConfig, *, queue: PostgresJobQueue | None = None,
                 registry: AdapterRegistry | None = None, max_parallel_supplier_jobs: int = 2,
                 poll_seconds: float = 2.0, lease_factory: Callable | None = None) -> None:
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

    def recover(self) -> int:
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
                self.queue.complete(job.id, result.get("status", "succeeded"), crawl_run_id=result.get("crawl_run_id"))
            except Exception as error:
                self.queue.complete(job.id, "failed", error_message=f"{type(error).__name__}: {str(error)[:2000]}")
            finally:
                stop.set()
                heart.join(timeout=1)

    def tick(self) -> int:
        self.active = {future for future in self.active if not future.done()}
        submitted = 0
        while len(self.active) < self.max_parallel:
            job = self.queue.claim_next()
            if job is None:
                break
            self.active.add(self.pool.submit(self._run_job, job))
            submitted += 1
        return submitted

    def run_forever(self) -> None:
        self.recover()
        while True:
            self.tick()
            time.sleep(self.poll_seconds)


def main() -> None:
    config = PostgresConfig.from_env()
    maximum = int(os.environ.get("MAX_PARALLEL_SUPPLIER_JOBS", "2"))
    Orchestrator(config, max_parallel_supplier_jobs=maximum).run_forever()


if __name__ == "__main__":
    main()
