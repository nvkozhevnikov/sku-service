"""Liveness of background loops (scheduler, orchestrator) stored in component_heartbeats (migration 017).

Each loop records one heartbeat per tick. Successful ticks are written at most every
``min_write_interval_seconds``; failures are always written. Recording never raises, so a
database outage cannot stop the loop it observes. ``/health`` and the container healthcheck
(``python -m universal_supplier.control_plane.heartbeat orchestrator``) read the table.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import sys
import time

from universal_supplier.postgres import PostgresConfig

LOGGER = logging.getLogger(__name__)
COMPONENTS = ("scheduler", "orchestrator")
FAILING_AFTER_CONSECUTIVE_ERRORS = 3
MIN_STALE_SECONDS = 120.0


def stale_after_seconds(poll_seconds: float, min_write_interval_seconds: float = 30.0) -> float:
    return max(MIN_STALE_SECONDS, min_write_interval_seconds + 4 * float(poll_seconds))


def component_status(row: dict | None, now: datetime) -> dict:
    """Classify one heartbeat row: ok, failing (repeated tick errors), stale (loop silent) or not_running."""
    if row is None:
        return {"status": "not_running"}
    age = (now - row["last_tick_at"]).total_seconds()
    if age > stale_after_seconds(row["poll_seconds"]):
        status = "stale"
    elif row["consecutive_failures"] >= FAILING_AFTER_CONSECUTIVE_ERRORS:
        status = "failing"
    else:
        status = "ok"
    return {"status": status, "last_tick_at": row["last_tick_at"].isoformat(), "seconds_since_tick": round(age, 1),
            "last_success_at": row["last_success_at"].isoformat() if row["last_success_at"] else None,
            "consecutive_failures": row["consecutive_failures"], "last_error": row["last_error"]}


class HeartbeatRecorder:
    def __init__(self, config: PostgresConfig, component: str, poll_seconds: float, *,
                 min_write_interval_seconds: float = 30.0, clock=time.monotonic) -> None:
        if component not in COMPONENTS:
            raise ValueError(f"unknown component: {component}")
        self.config, self.component, self.poll_seconds = config, component, float(poll_seconds)
        self.min_write_interval_seconds, self.clock = min_write_interval_seconds, clock
        self._last_success_write: float | None = None

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), autocommit=True)

    def record(self, error: BaseException | None = None) -> None:
        if error is None and self._last_success_write is not None \
                and self.clock() - self._last_success_write < self.min_write_interval_seconds:
            return
        try:
            with self._connect() as c, c.cursor() as q:
                if error is None:
                    q.execute("""INSERT INTO component_heartbeats(component,last_tick_at,last_success_at,consecutive_failures,poll_seconds)
                                 VALUES (%s,now(),now(),0,%s)
                                 ON CONFLICT (component) DO UPDATE SET last_tick_at=now(),last_success_at=now(),
                                   consecutive_failures=0,poll_seconds=EXCLUDED.poll_seconds,updated_at=now()""",
                              (self.component, self.poll_seconds))
                else:
                    message = f"{type(error).__name__}: {str(error)[:500]}"
                    q.execute("""INSERT INTO component_heartbeats(component,last_tick_at,last_error_at,last_error,consecutive_failures,poll_seconds)
                                 VALUES (%s,now(),now(),%s,1,%s)
                                 ON CONFLICT (component) DO UPDATE SET last_tick_at=now(),last_error_at=now(),last_error=EXCLUDED.last_error,
                                   consecutive_failures=component_heartbeats.consecutive_failures+1,poll_seconds=EXCLUDED.poll_seconds,updated_at=now()""",
                              (self.component, message, self.poll_seconds))
        except Exception:
            LOGGER.warning("could not record %s heartbeat", self.component, exc_info=True)
            self._last_success_write = None
            return
        self._last_success_write = self.clock() if error is None else None

    def clear(self) -> None:
        """Remove the row of a component that is intentionally not running (e.g. SCHEDULER_ENABLED=NO)."""
        try:
            with self._connect() as c, c.cursor() as q:
                q.execute("DELETE FROM component_heartbeats WHERE component=%s", (self.component,))
        except Exception:
            LOGGER.warning("could not clear %s heartbeat", self.component, exc_info=True)


def read_components(cursor) -> dict:
    """Status of every known component from an open dict_row cursor."""
    cursor.execute("SELECT component,last_tick_at,last_success_at,last_error,consecutive_failures,poll_seconds FROM component_heartbeats")
    rows = {row["component"]: row for row in cursor.fetchall()}
    now = datetime.now(timezone.utc)
    return {component: component_status(rows.get(component), now) for component in COMPONENTS}


def main(argv: list[str] | None = None) -> int:
    """Container healthcheck: exit 0 only when the named component's heartbeat is ok."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in COMPONENTS:
        print(f"usage: python -m universal_supplier.control_plane.heartbeat {{{'|'.join(COMPONENTS)}}}", file=sys.stderr)
        return 2
    import psycopg
    try:
        with psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row) as c, c.cursor() as q:
            status = read_components(q)[args[0]]
    except Exception as error:
        print(f"{args[0]}: heartbeat unavailable: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"{args[0]}: {status['status']}")
    return 0 if status["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
