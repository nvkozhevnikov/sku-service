# Unified readiness —2026-10-20
REPOSITORY-VERIFIED code continuation after c4424f28. See workspace reports/RC_UNIFIED_READINESS_CODE_20261020: REPORT, SQL_QA_PLAN, GROK_CLASSIFICATION, SOURCE_READINESS_MATRIX, DEVELOPER_HANDOFF and machine receipts. Historical reports are preserved, not overwritten.

A presence/quality dedup uses atomic JSONB append suppression and crawl-run row locking, no migrations/CHECK weakening/deletion. B one create_app/routes/queue with real job/error/phases/history timestamps. C scoped source SQL loading + nested timers, complete canonical universe and exact old/new plans. D prepared per-host pool using current rate limiter/lease contract; E prepared durable frontier/resume classifier using KAMI atomic writer. Neither D/E is operational full discovery for all collectors. F existing LIVE UTC timestamp is correct; only regression assertions added.

One panel/one PostgreSQL remains target. Eleven registered supplier runners are offline READY; current-code live HTTP+SQL+matching chain NOT_VERIFIED, including Partner after this patch. Historical Partner HTTP50 and separate SQL QA are separate evidence.12 competitors preserved, Youmet manual. Scheduler/cron not enabled. UI source wrappers remain bounded manifest refresh, not full discovery. SourceCheckpoint.complete is a caller-evidence predicate, never external catalog completeness proof.

Current unchanged test data:18958products/offers,1282links,25890matches,18120identitydecisions,17672reviewcases,201history,2qualitysnapshots. Old50duplicate SEEN and redundant quality remain; expected new QA delta0, no cleanup. Existing dumps unchanged. No HTTP/SQL writes/new dump. Own cluster started read-only for golden then smart-stopped.

SQL QA proposed only exact127.0.0.1:55461/rc_verify_vi_20261019/sys7698346631476390904, separate permission first. Existing MainRC/source DB/other trees not changed. Linux3.12 separate gate; tests this task Python3.12.14 Windows. No automatic handoff to develop.

Final code tested e37a8a59ae72af8d514fa4d71e48bdafd78e108d:836PASS/11SKIP/2legacy FAIL, both reproduced on c4424f28; not an overall green suite. Components99PASS/1SKIP. See BASELINE_V22/XML and QA_SUMMARY; guards/tests not weakened.
