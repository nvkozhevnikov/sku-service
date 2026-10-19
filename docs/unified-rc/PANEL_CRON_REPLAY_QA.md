# Existing panel and replay dispatch

LIVE-VERIFIED copied DB: one existing FastAPI application; existing shared
login/role/session/CSRF/origin guards; supplier and native competitor routes.
No iframe, second login or separate customer panel.

Real Edge browser: supplier/products, competitor news/prices, replay page,
actual form enqueue and separate replay process PASS; external requests0.
UI process stopped after QA; screenshots remain reports/integration/PANEL_*.png.

TestClient+realcopiedDB: shared login/logout, unauthenticated rejection,
cross-origin/CSRF rejection, live-start block, manualdedup, fixedUTCdue-slot
dedup,22source replay results and1explicitmissing Vekprom PASS. All source rows
unchanged. Dedicated role rejects wrongDB/user/port/search_path and cross-schema
read/write privileges. Report caps retain explicit overflow failure; observed
20285campaign rows required raising the read bound from20000to25000.

One FrozenDispatch path serves manual UI/CLI and scheduled due simulation.
It persists queued/running/succeeded records and uses transaction advisory
locks plus unique active-source/due-slot keys. It reads historical database
payloads only; it does not claim parser re-extraction, matching or live crawl.
Existing supplier orchestrator and competitor detached collector retained.

No daemon/cron installed. All11supplier namespaces and12competitors declared.
Only Partner-ST/Optimum have existing shared live orchestrator runners. Others
retain extraction/collection components; all-site LIVE cron readiness remains
NOT_VERIFIED. YouMet remains manual-capture. Runtime/source matrices say this
explicitly. Replay CLI: scripts/rc_runtime.py --work-one, local ownedDB only.
