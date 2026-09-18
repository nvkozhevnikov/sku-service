from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from universal_supplier.postgres import PostgresConfig
from .queue import PostgresJobQueue


DEFAULT_TIMEZONE = "UTC"


def schedule_to_controls(expression: str | None) -> dict[str, object]:
    value = (expression or "").strip()
    parts = value.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"]:
        time_value = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
        if parts[4] == "*":
            return {"mode": "daily", "time": time_value, "weekdays": []}
        if all(x in "0123456," for x in parts[4]):
            return {"mode": "weekdays", "time": time_value, "weekdays": parts[4].split(",")}
    return {"mode": "custom", "time": "02:00", "weekdays": [], "custom_cron": value}


def controls_to_schedule(mode: str, time_value: str, weekdays: list[str], custom_cron: str) -> str:
    if mode == "custom":
        return custom_cron.strip()
    try:
        hour_text, minute_text = time_value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as error:
        raise ValueError("укажите время в формате ЧЧ:ММ") from error
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("укажите корректное время")
    day = "*"
    if mode == "weekdays":
        valid = sorted({value for value in weekdays if value in {"0","1","2","3","4","5","6"}}, key=int)
        if not valid:
            raise ValueError("выберите хотя бы один день недели")
        day = ",".join(valid)
    elif mode != "daily":
        raise ValueError("неизвестная периодичность")
    return f"{minute} {hour} * * {day}"


def validate_schedule(cron_expression: str, timezone_name: str) -> None:
    if not croniter.is_valid(cron_expression):
        raise ValueError("invalid cron expression")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {timezone_name}") from error


def next_occurrences(cron_expression: str, timezone_name: str, *, after: datetime | None = None,
                     count: int = 3) -> list[datetime]:
    validate_schedule(cron_expression, timezone_name)
    zone = ZoneInfo(timezone_name)
    base = after or datetime.now(timezone.utc)
    if base.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    local = base.astimezone(zone)
    iterator = croniter(cron_expression, local)
    return [iterator.get_next(datetime).astimezone(timezone.utc) for _ in range(count)]


def due_occurrence(cron_expression: str, timezone_name: str, now: datetime) -> datetime:
    validate_schedule(cron_expression, timezone_name)
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    return croniter(cron_expression, local + timedelta(microseconds=1)).get_prev(datetime).astimezone(timezone.utc)


@dataclass(frozen=True)
class SchedulerPolicy:
    poll_seconds: float = 15.0
    misfire_grace_seconds: float = 60.0


class Scheduler:
    def __init__(self, config: PostgresConfig, queue: PostgresJobQueue,
                 policy: SchedulerPolicy | None = None) -> None:
        self.config = config
        self.queue = queue
        self.policy = policy or SchedulerPolicy()

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), autocommit=True)

    def tick(self, now: datetime | None = None) -> list[int]:
        now = now or datetime.now(timezone.utc)
        enqueued = []
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """SELECT ss.supplier_id,ss.cron_expression,ss.timezone,ss.crawl_type,ss.last_enqueued_for
                   FROM supplier_schedules ss JOIN suppliers s ON s.id=ss.supplier_id
                   WHERE ss.enabled AND s.enabled FOR UPDATE OF ss"""
            )
            for supplier_id, expression, timezone_name, crawl_type, last_enqueued in cursor.fetchall():
                occurrence = due_occurrence(expression, timezone_name, now)
                age = (now - occurrence).total_seconds()
                should_enqueue = age <= self.policy.misfire_grace_seconds and (last_enqueued is None or occurrence > last_enqueued)
                if should_enqueue:
                    result = self.queue.enqueue(supplier_id, trigger_type="schedule", crawl_type=crawl_type, scheduled_for=occurrence)
                    if result.enqueued:
                        enqueued.append(result.job_id)
                    cursor.execute("UPDATE supplier_schedules SET last_enqueued_for=%s,last_evaluated_at=%s,updated_at=now() WHERE supplier_id=%s", (occurrence, now, supplier_id))
                else:
                    cursor.execute("UPDATE supplier_schedules SET last_evaluated_at=%s,updated_at=now() WHERE supplier_id=%s", (now, supplier_id))
        return enqueued

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(self.policy.poll_seconds)
