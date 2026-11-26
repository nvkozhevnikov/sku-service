# Competitor monitoring: scheduler READY but DISABLED

OPERATOR-CONFIRMED scope: prepare code/configuration; install or activate nothing.
This document describes future steps only. No cron, Windows task, systemd timer,
GitHub Action, scheduled Codex automation or background collection daemon is installed.

## One execution contract

`competitor_intel.execution.run_competitor_collection` is the shared use-case.
UI POST → CollectionService.run(OPERATOR_UI); CLI collect/crawl → CLI_MANUAL;
CLI run-scheduled → SCHEDULED. The same collector, dedicated Store,
existing extractors, normalized_dataset/change detector and HTML export are used.
Legacy lower-level runners remain for their existing tests; CLI no longer invokes
their independent run paths. Legacy `crawl --output` is superseded by the common
runtime/report directory; browser/date overrides require a separately reviewed
configuration rather than a second runner.

No initialization/migration is performed by this runner. Runtime dependencies
are the existing requirements. Missing schema/DB is FAILED; no supplier DSN fallback.

## Configuration and execution

Committed default configuration: `competitor_intel/schedule.json`:
enabled=false, timezone=Europe/Moscow, time_of_day=08:00, six selected sources,
skip_if_running=true, max_runtime=1800 seconds, max_pages_per_source=45,
failure_policy=CONTINUE_OTHER_SOURCES_NO_RUN_RETRY. Types/bounds/zone are validated.

From the verified Windows feature checkout:

```powershell
# Manual execution, after authorization for collection:
& '.\.venv\Scripts\python.exe' -m competitor_intel --dev-config competitor-data/dev-config.json collect --source metalmaster --max-pages 1
# Future scheduled entrypoint: returns CONFIG_ERROR while default enabled=false:
& '.\.venv\Scripts\python.exe' -m competitor_intel --dev-config competitor-data/dev-config.json run-scheduled
```

UI mode must be explicitly requested with `python -m scripts.run_competitor_monitoring_ui
--allow-collection`. Default launcher remains read-only. Local opt-in allows only
the competitor collection POST; supplier/settings mutations remain forbidden.
Same-origin form, authenticated ADMIN in the normal application, existing CSRF
contract and bounded URL-encoded body protect that route. The local review profile
allows the operator action only with explicit loopback opt-in.

Credentials: COMPETITOR_INTEL_DSN environment or ignored dev-config JSON referenced
by path. No secret value in command arguments, UI, logs or Git. Store currently
accepts only competitor_dev / 127.0.0.1:55454 / sterbrust_competitor_intel. A future
production connection change needs separate scope/approval; templates below do
not authorize it. Linux deployment paths/runtime have not been LIVE-VERIFIED.

## Lock, origins, persistence and limits

Shared kernel lock: absolute feature checkout `competitor-data/collection/collection.lock`.
Windows msvcrt / Unix flock. Every UI/CLI invocation uses the same directory,
irrespective of config path/source selection. Crash releases the kernel lock;
the file is never deleted to steal a lock. A previous RUNNING journal is marked
FAILED/PROCESS_INTERRUPTED on the next successful acquisition.

Existing competitor_runs stores origin in result JSON, with RUNNING immediately
and COMPLETED/PARTIAL/FAILED at finish. No schema change. Observations use that
single run ID. Source failures do not roll back already committed observations.
Report rebuild still uses the existing read-only immutability proof.

| Exit | Status |
|---|---|
| 0 | COMPLETED |
| 1 | PARTIAL, some accepted results preserved |
| 2 | FAILED, execution/report/persistence failed |
| 3 | SKIP_ALREADY_RUNNING |
| 4 | CONFIG_ERROR, including SCHEDULER_DISABLED |

HTTP uses one sequential worker, existing stable UA and 3–7s pacing; existing
bounded Retry-After/backoff policy; protection responses stop that source.
Robots evidence is independent from actual fetch status. No authentication,
private API, proxy rotation or CAPTCHA bypass. Unattended browser rendering is
not added: render-required pages are PARTIAL/FAILED rather than empty success.
Known observed navigation defines surfaces; no full catalog/product crawl.
JET promo pagination is followed within the configured page cap; cap exhaustion
is explicitly PARTIAL. No disappearance is inferred from this incomplete scope.
YouMet is MANUAL_PENDING with zero HTTP: saved HTML is imported through the existing
operator workflow. Its manual mode does not become an automatic failure.

Max runtime is a cooperative collection deadline: checked before pages, sleeps,
retries and requests; connect/read timeout budgets use remaining time. Report
finalization still runs to preserve results; this is not an OS-level hard kill.
There is no automatic restart of an interrupted run and no retry loop for a run.
KAMI uses the existing exact-URL historical cache only for context; refresh targets
are deferred, product-detail GETs=0. Historical seed is never rebuilt by this runner.

## Logs and health

`competitor-data/collection/collection.log`: 2 MiB × current+4 backups maximum.
No new background logging service. Rotated log records contain run/source/status,
never exception messages/DSNs. last-run.json/manual-last.json/scheduled-last.json
are atomic, bounded single journals. Business report rebuild overwrites one common
business-report directory. Per-run raw captures are evidence referenced by DB;
they are not loose logs and are not deleted blindly. Future evidence archival must
preserve those references and needs its own retention decision.

UI shows manual readiness, last manual run, last automatic run and disconnected
schedule. Default last automatic=Never and next=Planner not connected; no fictitious
next_run_at. enabled=true alone does not install a timer. This preparation contains
no polling loop or automatic start/restart-on-boot.

## Future Windows Task Scheduler example — NOT APPLIED

Verified environment:
Program: `D:\documents\personal\ChatGPT\Стербруст\work\sterbrust-competitive-intelligence\.venv\Scripts\python.exe`

Arguments: `-m competitor_intel --dev-config competitor-data/dev-config.json run-scheduled`

Start in: `D:\documents\personal\ChatGPT\Стербруст\work\sterbrust-competitive-intelligence`

Trigger: daily 08:00 Europe/Moscow business time. Windows trigger time must be
mapped explicitly to the workstation timezone; the Python setting does not
change a Windows trigger. Verify UTC+03/MSK against that machine on installation.
Choose Do not start a new instance, no aggressive restart and a bounded task limit
that allows finalization. Ensure the dedicated DB is available without silently
starting other stacks. This is an example action/trigger specification, not an
installed task. [Microsoft Task Scheduler](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page).

## Future Linux cron example — NOT INSTALLED

Templates, replace only after environment verification:

```cron
CRON_TZ=Europe/Moscow
0 8 * * * cd <verified-checkout> && <verified-python> -m competitor_intel --dev-config <protected-config-path> run-scheduled
```

CRON_TZ support depends on installed cron implementation; verify it before using.
Do not hardcode Windows paths on Linux or assume every cron supports CRON_TZ.
Do not append unbounded stdout files; use the bounded application log and the
host's reviewed logging policy. [crontab timezone documentation](https://man7.org/linux/man-pages/man5/crontab.5.html).

## Future systemd example — NOT INSTALLED

```ini
# competitor-monitoring.service — template only
[Unit]
Description=Competitor monitoring bounded collection
[Service]
Type=oneshot
WorkingDirectory=<verified-checkout>
ExecStart=<verified-python> -m competitor_intel --dev-config <protected-config-path> run-scheduled
TimeoutStartSec=35min
SuccessExitStatus=1 3
```

```ini
# competitor-monitoring.timer — template only
[Unit]
Description=Competitor monitoring daily MSK
[Timer]
OnCalendar=*-*-* 08:00:00 Europe/Moscow
Persistent=false
Unit=competitor-monitoring.service
[Install]
WantedBy=timers.target
```

Validate syntax/timezone on the actual target systemd version before installation.
PARTIAL remains recorded even where exit 1 is an accepted service outcome. Do not
configure Restart=always. Environment/platform installation has not been performed.

## Enable contract

Scheduler is READY but DISABLED. Production schedule needs separate operator
approval, environment/path/timezone verification, reviewed enabled=true config
and exactly one documented scheduler installation. Until then: scheduler_enabled=false,
cron installed=NO, Windows task created=NO, automatic live run executed=NO.
Fixture scheduled CLI tests use only temporary configuration/fake repository and
the actual report/change engine; they do not activate a schedule or touch live sources.
