# Current status correction —2026-10-20

REPOSITORY-VERIFIED: historical text below describes registration-time state. Permanent matching HOLD has been superseded: source_jobs invokes scoped run_post_ingest for PERSISTED external IDs; Partner/Optimum use existing automatic safe pipeline. No old reconcile. F01/F03/confirmed IDs retained. Current9wrapper remains bounded manifest refresh, sequential1worker/≥20s AFTER response/timeout≤20; no full discovery proof. New host_fetch and source_resume are code-only prepared components, not runtime-enabled. See docs/UNIFIED_READINESS_20261020.md and workspace evidence for exact SQL/live readiness, current baseline and next gates. Latest code does not itself authorize HTTP/SQL. Youmet manual.

---

# Shared bounded source runners

Existing AdapterRegistry/Orchestrator/queue/scheduler now bind all11supplier
namespaces. Original Partner-ST/Optimum jobs are retained. Added wrappers reuse
existing Beka RU/TR,Intervesp,Vekprom,KAMI,LTT MSK/SIB/YUG,USS parsers and the
existing narrow persist_commercial_observation / persist_kami_observation writers.
No parser was rewritten; no matching, selection or deactivation is performed.

Manual and scheduled jobs use the same registration, immutable settings snapshot,
domain lease, queue dedup and heartbeat. Jobs remain bounded refreshes of verified
manifest URLs, not a claim of complete discovery. Existing discovery workflows
remain the authority for candidate manifests. Disabled suppliers/schedules were
not enabled. Actual SQL/HTTP collection was not executed by this task.

The snapshot's crawl_settings requires source_manifest_path and
source_manifest_sha256. Manifest format source-job-manifest/v1 contains
supplier_code and nonempty entries: url,source_external_id,file,sha256,
observed_at with timezone,http_status200,content_type,charset. Evidence files
must resolve under the manifest directory and match SHA256. URLs and redirects
are checked against each existing source allowlist; duplicate URLs/IDs fail.
The manifest cannot be used as proof that injected fixture metadata was real HTTP.

Default execution_mode=OFFLINE_SAVED,persist_source_data=false. Offline keeps
recorded timestamps and does not create a transport/repository. Persistence also
requires original capture metadata with matching evidence hash/time/URLs; it
cannot turn pasted HTML with unknown headers/status into a verified capture.

Future network mode LIVE additionally requires runtime
STERBRUST_ALLOW_SUPPLIER_HTTP=YES. Source writes require persist_source_data=true
and STERBRUST_ALLOW_SOURCE_WRITES=YES. These permissions were not enabled in the
business runtime. Tests inject local saved responses and an in-memory writer.
Production persistence remains subject to existing linked-source guards; those
guards are retained and an uncertain write fails the job. No SQL acceptance claim
is inferred from fake-writer tests. No new operational manifest/config was written
into RC1 or any business database.

Limits default100products/300seconds,max1000products/3600seconds. Partial scope,
deadline,REVIEW,403/429 and interstitials never claim full discovery or deactivate
missing products. The public client retains source-specific URL/redirect checks
and >=20s request pacing. Source ID changes/multi-variant USS remain REVIEW.

In the pinned isolatedRC1 connection or explicit STERBRUST_UNIFIED_RC profile,
the original Partner-ST/Optimum post-ingest identity step is held as
RC_RELEASE_IDENTITY_HOLD. New wrappers never invoke reconcile. Stored links and
Product IDs remain authoritative until separate evidence/approval.

Linux gate: scripts/qa_rc_linux_offline.py refuses Windows/non3.12 and blocks all
network and PostgreSQL connections in-process. Run only inside a separately
provided Linux3.12 sandbox with dependencies preinstalled and no production
mounts/secrets. No Linux execution was possible on the current stopped
docker-desktop-only host. Prepared gate code is not Linux QA evidence.
