# Partner-ST bounded pilot

Existing supplier page: Test run, integer 1..50. Full mode unchanged outside explicit
pilot runtime. Queue stores mode/limit/registered source PK/external ID/URL and digest
in immutable job settings_snapshot. Worker revalidates registry and budget before
claiming. Actual run_partner_st_live skips discovery and fetches only this allowlist;
redirects outside it fail closed. 1 connection, >=2 seconds, one attempt, <=300 seconds.

Pilot process: STERBRUST_PARTNER_PILOT=YES, STERBRUST_ALLOW_SUPPLIER_HTTP=YES,
STERBRUST_RC_MATCHING_APPLY_APPROVED=YES; PARTNER_PILOT_EVIDENCE_ROOT and
PARTNER_PILOT_STOP_FILE must point into the authorized evidence directory.
Existing scripts/rc_runtime.py runs the same panel; --pilot-work-one consumes at most
one real shared job without scheduler/recovery/forever mode. Do not use --work-one
(FrozenDispatch). Existing full collection remains separate and blocked in pilot runtime.

All write-capable panel/worker connections validate exact 127.0.0.1:55461/
rc_verify_vi_20261019, system 7698346631476390904, PG17.11 and PGDATA before enabling
writes; a mismatched cluster stays read-only and closes. Never target main RC1.

Bounded ingest skips global navigation/property reconciliation and all missing
consequences. Unknown price cards do not overwrite old values. Successful cards,
real sanitized HTTP evidence, append-only capture/commercial observations and
change history are retained. Response hash explicitly labels decoded UTF-8 basis,
not original wire bytes. Automatic matching uses only successful external IDs and
preserves existing confirmed IDs/F01/F03. Stop button stops scheduling new requests;
in-flight request may finish. No automatic retry or job replay after worker restart.

This pilot verifies a refresh of saved URLs, not full discovery or other suppliers.
Backup/preflight and separate operator authorization are required before LIVE.
