# Future daily competitor reporting — plan only

NOT ACTIVATED. No cron, Windows Task Scheduler, GitHub Actions, daemon, service,
heartbeat or recurring task is created in this work. Operator report review first.

After separate approval and useful-report acceptance, the proposed flow is:

1. Acquire a single-run lock. Verify exact dedicated competitor DB/source policy,
   source capabilities, eligible freshness window and pinned extractor versions.
2. Bounded crawl of approved enabled competitors, normal public access only.
   Retain raw source evidence, rates/jitter, error/coverage flags. Never integrate
   with Universal Supplier, production, ESOL or Sterbrust matching implicitly.
3. Transactionally persist source facts, meaningful versions and observations.
   Declare discovery/rendered-scope completeness only when actually verified;
   unknown or failed coverage cannot prove campaign/product disappearance.
4. Rebuild deterministic change events from accepted comparable observations.
   New extraction scope starts BASELINE, not new-products notifications.
5. Optional AI on saved versions only, separately authorized/configured; provider
   failures preserve crawl/report facts. Default remains disabled.
6. Generate the Moscow-date daily standalone HTML/CSV/JSON into a temporary
   output directory, validate schemas/ids/price semantics/source counts, then
   publish the completed local directory atomically. Keep previous valid artifact
   on failure. No empty success report when the collector failed.
7. Keep dates/freshness and missing capabilities visible. Retain raw captures,
   event keys and export hashes. Store secrets outside Git and the report.

Retries bounded; no simultaneous workers for one source, no activation of unrelated
runtime/containers, no anti-bot/access-control evasion. Actual transport policy is
source/workflow-specific. New source/scheduler/notification permissions are separate.

Initial delivery ends WAITING_FOR_OPERATOR_REPORT_REVIEW. Next scope is report/UX
corrections and additional competitors. Scheduling is decided after those gates.
