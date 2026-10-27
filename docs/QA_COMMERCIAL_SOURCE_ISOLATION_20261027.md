# Commercial REFRESH: source isolation regression

REPOSITORY-VERIFIED, offline. No real HTTP, enqueue, SQL ingest or matching apply.
733 PASS / 0 FAIL / 9 deselected (B0 cases) / 4 subtests PASS.
One existing Starlette/httpx deprecation warning; no skipped acceptance failures.
JUnit: workspace reports/COMMERCIAL_SOURCE_ISOLATION_20261027/OFFLINE_QA.xml.

Focused incident/continuation cases:46 PASS (included in733, not additive).
Actual saved inputs/checkpoints/results of jobs137/138 are gzip fixtures, not synthetic LIVE.
Injected response scenario: Beka robots200, Intervesp URLError(ConnectionResetError),
Beka product committed by an offline backend double, Intervesp product calls0,
Vekprom continues; all other9 adapter fixtures independently continue as well.
No durable SQL commit is claimed from these tests.

Additional checks: exception reason/cause/errno/redaction; TLS certificate refusal;
robots404/HTML/crawl-delay; same-host pause; archive disk failure and global STOP;
unknown product outcome and no retry; existing scheduler source-local vs systemic branches.
Saved zero-product continuation rejects COMMITTED, cached, UNCERTAIN, QUARANTINED,
inflight, any product dispatch, SQL commit or input scope drift.
Existing all11 native RESUME no-repeat, approved1282 guard, temporary settings CAS,
hourly local reports, linked commercial writer, semantic/F01/F03, matching and XML
independent freshness regression remain PASS. B0 SQL QA / Partner-ST LIVE50 not run.

Command family: pytest tests/test_supplier_network_isolation.py
tests/test_commercial_continuation.py tests/test_host_fetch.py tests/test_automation_execution.py
tests/test_commercial_recovery.py tests/test_source_runtime.py tests/test_source_runtime_controls.py
tests/test_native_checkpoint.py tests/test_source_resume.py tests/test_source_canary.py
tests/test_source_linked_update.py tests/test_optimum_source.py tests/test_commercial.py
tests/test_commercial_persistence.py tests/test_automatic_matching.py tests/test_f01_f03_identity_inputs.py
tests/test_kami_matching.py tests/test_xml_coverage_recovery.py tests/test_automation_system.py
tests/test_full_supplier_collection.py tests/test_source_jobs.py -q -k "not b0".

Separate LIVE-VERIFIED SELECT proof (not offline PASS): exact55461 identity,
original1282 and all physical mappings unchanged, capture/observation/history deltas0,
old jobs protected, all11 settings restored, activejobs0. No new supplier HTTP was made.
Actual remote reason behind the old Intervesp URLError remains UNKNOWN.
