from __future__ import annotations

from universal_supplier.partner_pilot import connect_postgres

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from universal_supplier.postgres import PostgresConfig
from .queue import PostgresJobQueue


DEFAULT_TIMEZONE = "UTC"
LOGGER = logging.getLogger(__name__)


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
                 policy: SchedulerPolicy | None = None, *, heartbeat=None, automation=None) -> None:
        self.config = config
        self.queue = queue
        self.policy = policy or SchedulerPolicy()
        self.heartbeat = heartbeat
        self.automation=automation
        self.blocked={}

    def _connect(self):
        import psycopg
        if self.automation is not None:
            from .runtime_config import guarded_connect
            return guarded_connect(self.config,autocommit=True)
        return connect_postgres(self.config, autocommit=True)

    def tick(self, now: datetime | None = None) -> list[int]:
        now = now or datetime.now(timezone.utc)
        if self.automation is not None:return self._tick_sources(now)
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

    def _tick_sources(self,now):
        """Two modes, same queue and leases; never drives the frozen LIVE11 batch."""
        from .commercial_refresh import is_refresh,tick
        if is_refresh(self.automation.value):return tick(self,now)
        from .supplier_automation import settings_for,due_occurrence,digest
        from .automation_execution import require_active,require
        from .source_failure import ROBOTS_LOCAL
        session=require_active(self.automation.sha)
        result=[];self.blocked={}
        with self._connect() as c,c.cursor() as q:
            q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code')
            rows=q.fetchall()
        for sid,code,enabled,rate,crawl in rows:
            if code not in session.value['suppliers']:continue
            paused=session.source_reason(code) if hasattr(session,'source_reason') else None
            if paused:self.blocked[code]=paused;continue
            cfg=settings_for({'code':code,'rate_settings':rate,'crawl_settings':crawl})
            if not enabled or not cfg['schedule_enabled']:continue
            approved=session.value['suppliers'][code]
            if approved.get('settings_sha256') and digest(cfg)!=approved['settings_sha256']:
                self.blocked[code]='AUTOMATION_SOURCE_SETTINGS_CHANGED: новый допуск для новой ревизии';continue
            # A source refusal applies to both phases. In particular DISCOVERY
            # must not bypass an earlier REFRESH403 (or vice versa) on a restart.
            with self._connect() as c,c.cursor() as q:
                phase_sql="coalesce(j.settings_snapshot->'automation'->>'phase',j.settings_snapshot->'source_batch'->>'phase',j.settings_snapshot->'source_input'->>'mode')"
                q.execute(f'''SELECT DISTINCT ON ({phase_sql}) j.status,j.settings_snapshot,r.metadata,j.error_message
                    FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.supplier_id=%s
                    AND {phase_sql} IN ('REFRESH','DISCOVERY')
                    ORDER BY {phase_sql},j.id DESC''',(sid,))
                latest=q.fetchall()
            for state,snap,metadata,error in latest:
                if state in ('succeeded','queued','running','skipped_already_queued'):continue
                marker=snap.get('automation') or {};rt=(metadata or {}).get('source_runtime') or {}
                if snap.get('source_batch'):
                    self.blocked[code]='LIVE11_REQUIRES_SEPARATE_RECOVERY';break
                if marker.get('approval_sha256')!=session.sha:
                    self.blocked[code]='PREVIOUS_EXECUTION_REQUIRES_OPERATOR';break
                reason=rt.get('termination_reason') or error or 'UNPROVEN_PARENT'
                if reason in ROBOTS_LOCAL or str(reason).startswith(('SOURCE_ROBOTS_','SOURCE_BLOCKED','SOURCE_CARD_ERROR_THRESHOLD','SOURCE_IDENTITY_MASS_FAILURE',
                    'OPERATOR_STOP','SOURCE_HTTP_CIRCUIT_BREAKER','SOURCE_NETWORK_CIRCUIT_OPEN','SOURCE_RETRY_AFTER_HTTP_503')):
                    self.blocked[code]=reason;break
                if rt.get('resume_uncertain') or rt.get('resume_failed') or (state in ('partial','failed','stale_failed') and not rt.get('resume_available')):
                    session.fail('AUTOMATION_CRITICAL_JOB:'+str(reason)[:150]);session.check()
            if code in self.blocked:continue
            for phase in ('REFRESH','DISCOVERY'):
                if not cfg[phase.lower()+'_enabled']:continue
                session.check()
                with self._connect() as c,c.cursor() as q:
                    q.execute("SELECT id FROM crawl_jobs WHERE supplier_id=%s AND status IN ('queued','running')",(sid,))
                    if q.fetchone():break
                    q.execute('''SELECT j.id,j.status,j.crawl_run_id,j.scheduled_for,j.settings_snapshot,j.error_message,r.metadata
                        FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.supplier_id=%s
                        AND coalesce(j.settings_snapshot->'automation'->>'phase',j.settings_snapshot->'source_batch'->>'phase',
                                     j.settings_snapshot->'source_input'->>'mode')=%s ORDER BY j.id DESC LIMIT 1''',(sid,phase))
                    previous=q.fetchone()
                due=due_occurrence(cfg,phase,now);resume=None
                if previous:
                    jid,state,run,scheduled,snap,error,metadata=previous
                    if state!='succeeded':
                        if snap.get('source_batch'):
                            self.blocked[code+':'+phase]='LIVE11_REQUIRES_SEPARATE_RECOVERY';continue
                        marker=snap.get('automation') or {}
                        if marker.get('approval_sha256')!=session.sha:
                            self.blocked[code+':'+phase]='PREVIOUS_EXECUTION_REQUIRES_OPERATOR';continue
                        rt=(metadata or {}).get('source_runtime') or {}
                        if run is not None:
                            if not rt.get('resume_available') or rt.get('resume_uncertain') or rt.get('resume_failed'):
                                reason=rt.get('termination_reason') or error or 'UNPROVEN_PARENT'
                                local=('SOURCE_ROBOTS_','SOURCE_BLOCKED_HTTP_403','SOURCE_BLOCKED_HTTP_429','SOURCE_CARD_ERROR_THRESHOLD',
                                       'SOURCE_IDENTITY_MASS_FAILURE','OPERATOR_STOP','SOURCE_HTTP_CIRCUIT_BREAKER',
                                       'SOURCE_NETWORK_CIRCUIT_OPEN','SOURCE_RETRY_AFTER_HTTP_503','SOURCE_BLOCKED_INTERSTITIAL')
                                if reason not in ROBOTS_LOCAL and not str(reason).startswith(local):
                                    session.fail('AUTOMATION_CRITICAL_JOB:'+str(reason)[:150]);session.check()
                                self.blocked[code+':'+phase]=reason;continue
                            self.queue.source_resume_status(jid)  # SQL + durable native proof, no HTTP.
                            resume=jid;due=scheduled
                        elif (state=='cancelled' and error in ('AUTOMATION_STOP','AUTOMATION_WINDOW_STOP')) or (
                            state=='skipped_locked' and error=='SAME_DOMAIN_LOCK_NOT_ACQUIRED'):
                            due=scheduled
                        else:
                            self.blocked[code+':'+phase]='UNSTARTED_PARENT_REQUIRES_OPERATOR';continue
                    elif (snap.get('automation') or {}).get('approval_sha256')==session.sha and scheduled>=due:
                        continue
                rule=session.value['suppliers'][code]
                # A fresh grant does not authorize replaying old calendar slots.
                # Resume keeps the already admitted occurrence inside its grant.
                if resume is None and (previous is None or previous[1]=='succeeded') and due<datetime.fromisoformat(session.value['not_before']):
                    continue
                try:
                    if resume is not None:
                        admitted=self.queue.enqueue(sid,trigger_type='schedule',crawl_type='full',scheduled_for=due,
                            source_mode='RESUME',resume_job_id=resume,automation_sha256=session.sha)
                    else:
                        preview=self.queue.preview_source_canary(sid,rule['limits'][phase],phase)
                        admitted=self.queue.enqueue(sid,trigger_type='schedule',crawl_type='full',scheduled_for=due,
                            source_mode=phase,source_limit=rule['limits'][phase],
                            source_preview_sha256=preview['confirmation_sha256'],automation_sha256=session.sha)
                except (ValueError,PermissionError) as error:
                    reason=str(error)
                    if reason.startswith(('HOST_PROFILE','EXPLICIT_HOST','AUTOMATION_SCOPE_GROWTH',
                        'AUTOMATION_QUEUE_ATTEMPTS_EXHAUSTED','Поставщик')):
                        self.blocked[code+':'+phase]=reason;continue
                    raise
                if admitted.enqueued:result.append(admitted.job_id)
        return result

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as error:
                LOGGER.exception("scheduler tick failed; retrying after %ss", self.policy.poll_seconds)
                if self.heartbeat: self.heartbeat.record(error)
            else:
                if self.heartbeat: self.heartbeat.record()
            time.sleep(self.policy.poll_seconds)
