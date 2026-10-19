# Vekprom/Intervesp included in isolated RC1 — 19.10.2026

OPERATOR-CONFIRMED: staging restore, rollback rehearsal, scoped migration,
idempotency, full dump/restore and exports. Original DBs, other developer trees,
HTTP, mass matching, push/merge/deploy/cron/ESOL remain outside authorization.

DONE/APPLIED: own PostgreSQL17.11/55461 only. Vekprom6834products+6834offers added;
Intervesp1642identities reused without duplicates.78products+78offers updated in
allowed commercial/timestamp/raw fields;2offers changed price/currency. Richer
existing identity/content retained;312archived payloads retain both full versions.
Added6915captures+6915observations;2119existing history pairs deduplicated exactly.
35022sourcePKs mapped by namespace/natural keys. No catalog ID or decision changed.

LIVE-VERIFIED: source staging51tables/40sequences exact; pre-image backup restored
to new empty DB and compared. RC1 products/offers18958,active18954,history19816;
118FKs/0orphans,valid constraints/indexes,0duplicate source identities. Other
namespaces/competitors unchanged. Second migration0inserts/0updates/0maps/0archives.
New dump133823099bytes SHA256
`a2c9e3fec764714f00648485dee7c72d7b158439a9863803a722b3080de083c2`.
New empty restore:56public+11competitor tables,50sequences,constraints/migration
ledger equal. Pre-migration dumpSHA256
`e8b02d3f429ba7a462e63231741b5ff4d976677358ccf7f5429e96d1256a0993`.
Original55452/cold backup1705files and previous delivery unchanged. OwnRC stopped.

Exports:373EXISTING,18581REVIEW/CONFLICT,18954TRACE,53news;NEW0/PRICE_UPDATES0.
461storedIDs remain in export REVIEW only,unchanged in DB. No new matching pass.
Zero-operation XLSX files retained byte-identically. Source panel uses verified
import-map coverage; both VI namespaces AVAILABLE,all live schedules disabled.
REPOSITORY-VERIFIED focused26testsPASS/Python3.12Windows; cache warning non-fatal.

BLOCKED: source omission resolved; matching churn/Linux/live-runner release gates
remain NOT_VERIFIED. Stage6BLOCKED_IMPORT_CONTRACT_EVIDENCE. NEXT owner review;
develop transfer requires separate approval.

Delivery: release/DELIVERY_2026-10-19_RC1_VI.
Full receipts/helper/report: workspace reports/RC1_VI_INCLUSION_20261019.
