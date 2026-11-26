# Worker startup boundary / proven unstarted continuation

REPOSITORY-VERIFIED offline:137 PASS,0FAIL in worker-boundary/source-isolation/
commercial-continuation/automation-execution/commercial-recovery suite.
Lease failure and a failed Future now persist sanitized evidence, complete the
queue intent as failed, stop the session and prevent the next claim.
Normal source-local robots results still allow independent suppliers to proceed.
No HTTP, SQL business writes or enqueue were used in QA.

Related worker/queue/heartbeat/sourcejobs regression:128PASS/2FAIL/1SKIP.
The two UI route expectations in test_review_fixes_control_plane.py
(test_unguarded_ints_return_400, test_full_xml_download_error_is_not_truncated_200)
also FAIL with prior9867 orchestrator/continuation modules loaded in-memory.
UI code is unchanged; no unrelated UI fix or broad audit was performed.
One opt-in SQL integration skip remains; no B0SQL or Partner-ST LIVE50 repeated.
JUnit evidence: workspace reports/COMMERCIAL_SOURCE_ISOLATION_20261027/EXECUTION_20261028/.

LIVE-VERIFIED SELECT after gracefulSTOP/native recover_stale:0active,
1282originalexact, oldjobs unchanged, all11settingsrestored,0newcaptures/observations.
Jobs145–150 retain native stale_failed;151–152 retain cancelled. No manual status
edits, checkpoint fabrication or UNCERTAIN replay. No run/input/response exists
for these jobs; the previous startup exception itself was not retained.
The fix proves error observability/safe failure handling, not the unknown primary
startup cause. Another real execution requires a new exactHEAD/SHA approval.
