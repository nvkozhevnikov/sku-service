# Partner-ST commercial history correction

Code/offline verification only; SQL apply and new HTTP require separate operator approval.

The historical money hash recipe changed Decimal spelling (integer vs two decimal places). Old snapshots contain JSON strings while new card values are Decimal: Python inequality could become equal JSONB after serialization and correctly fail entity_change_history_actual_change_ck.

Compare actual offer columns rather than a potentially stale raw_data snapshot. Normalize decimal spelling without rounding for commercial comparisons/history; preserve nonnumeric fields and nulls. Keep current hash recipes; update hashes only through normal per-card upsert. Insert history only for distinct serialized JSONB; count actual inserted rows. No migration or constraint changes, no ID or matcher changes.

Saved real50-card replay: commercial raw availability differs50, normalized availability32, quantity11, prices0; groups overlap.42 existing confirmed IDs preserved in matching dry-run; AUTO4 already preserved, REVIEW31/CONFLICT13/NEW2. Recording-cursor first commercial history50, repeat0; actual SQL application is pending.

HTTP receipt is persisted before SQL ingest. Failed runs show the SQL reason; request/parsing/SQL/history/matching timing is exposed, including aborted SQL duration. Test-mode enum rate profiles share the existing queue snapshot and worker validation; old jobs default conservative. Max50, exact test database guard, no discovery/deactivation and batch-only matching remain in force. Full mode stays separate.

Offline regressions395 PASS/1 SKIP (missing local docker-compose.qa.yml); no new HTTP, SQL write or Linux QA. External evidence and separately gated plans are in workspace reports/RC_PARTNER_ST_HISTORY_FIX_20261020. STOP before writes.
