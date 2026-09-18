from __future__ import annotations

import threading
import os

from universal_supplier.postgres import PostgresConfig
from .orchestrator import Orchestrator
from .queue import PostgresJobQueue
from .scheduler import Scheduler


DEFAULT_MAX_PARALLEL_SUPPLIER_JOBS = 2
MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND = 32


def read_max_parallel_supplier_jobs() -> int:
    raw_value = os.environ.get(
        "MAX_PARALLEL_SUPPLIER_JOBS", str(DEFAULT_MAX_PARALLEL_SUPPLIER_JOBS)
    ).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "MAX_PARALLEL_SUPPLIER_JOBS must be an integer between "
            f"1 and {MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND}; got {raw_value!r}"
        ) from error
    if not 1 <= value <= MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND:
        raise RuntimeError(
            "MAX_PARALLEL_SUPPLIER_JOBS must be between "
            f"1 and {MAX_PARALLEL_SUPPLIER_JOBS_UPPER_BOUND}; got {value}"
        )
    return value


def main() -> None:
    maximum = read_max_parallel_supplier_jobs()
    config = PostgresConfig.from_env()
    queue = PostgresJobQueue(config)
    scheduler = Scheduler(config, queue)
    orchestrator = Orchestrator(
        config,
        queue=queue,
        max_parallel_supplier_jobs=maximum,
    )
    if os.environ.get("SCHEDULER_ENABLED", "YES").upper() == "YES":
        thread = threading.Thread(target=scheduler.run_forever, name="cron-scheduler", daemon=True)
        thread.start()
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
