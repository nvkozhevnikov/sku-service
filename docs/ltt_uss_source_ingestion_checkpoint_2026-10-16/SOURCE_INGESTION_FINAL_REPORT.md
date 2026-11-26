# LTT/USS source-ingestion verified checkpoint — 2026-10-16

SOURCE INGESTION = VERIFIED. This is the SOURCE-INGESTION VERIFIED CHECKPOINT DATABASE.

DONE: sealed the accepted final baseline; created a portable PostgreSQL17.11 custom dump; restored it into a new isolated server on55457; repeated the accepted36-file test suite; audited all dirty feature paths for publication.

APPLIED: packaging and documentation only. No source repair was repeated and no corrective SQL ran. Restore initialization/restore writes affected only the new isolated rehearsal cluster; it is retained stopped.

VERIFIED: source PostgreSQL17.11/system7696153366151515704 on55455 matches PAGE_ROLE_REMEDIATION_2026-10-16/PAGE_ROLE_REVISED_BASELINE.json in all51 table fingerprints,40 sequences and2453 full product/offer pairs. Counts2453 products,2449 active,2453 offers,2533 captures,2533 observations. Articles225–228 are inactive, missed_crawls0; their historical offers remain inactive. Equality covers hotspot36,disk10,inverse97,kind29,operator cohorts,YUG identity/category and SIB special cases. Accepted FULL QA400/400 PASS (100/source), blockers0, RUN22634/2634, exact noop2449; these were not rerun. Fresh code tests4022 PASS +8 subtests; FAIL/ERROR/SKIP0.

Restore PASS, errors0. All51 table fingerprints,40 sequences,full product/offer projections and special cases equal source. Both servers are cleanly stopped; clients/open/prepared/workers0; ports55455/55457 closed; postmaster.pid absent. SAFE_TO_POWER_OFF=YES.

NOT APPLIED: matching NOT RUN; offer selection NOT RUN for this LTT/USS continuation; Sterbrust import NOT RUN; ESOL BLOCKED; production UNCHANGED; develop UNCHANGED; supplier HTTP0. Stage6 BLOCKED_IMPORT_CONTRACT_EVIDENCE. Next functional stage is LTT_USS_MATCHING_AGAINST_CANONICAL_STERBRUST and needs its own authorization.

OPERATOR-CONFIRMED: accepted QA and finalization authorization. REPOSITORY-VERIFIED: code seal, repeated tests, audit. PACKAGED-EVIDENCE: prior frozen source/400 semantic decisions. LIVE-VERIFIED: current SQL, dump/restore and shutdown. No separate LTT/USS finalizer exists; incompatible KAMI/aggregate/XML finalizers were not used.

Git scope: accepted application changes, offline recovery/QA utilities, tests and sanitized public-source fixtures, plus this durable package. No migrations or runtime configs were added. Credentials, .env, private/raw capture reports, PGDATA, dump, temp directories and full SQL projections remain external/ignored. Exact-path .gitattributes preserve accepted SHA256 bytes with Windows core.autocrlf=true; no business behavior changes. Accepted fixture provenance documents source DOM minimization/token removal.

The external final checkpoint records the final commit SHA and independent remote verification after feature-only publication, avoiding a self-referential commit hash. This committed snapshot records the pre-publication evidence. Never infer publication solely from this document. Full offline replay tests additionally require the preserved local frozen evidence under reports/; Git is not a replacement for that archive.

Backup path, size and SHA256: DB_BACKUP_MANIFEST.json. Restore: DB_RESTORE_VERIFICATION.json. Portable transfer procedure: install PostgreSQL17.11; create a new empty database in an isolated cluster; use pg_restore --no-owner --no-privileges --exit-on-error -d TARGET_DB BACKUP.dump; require matching counts/active flags and integrity. Do not restore over any existing Sterbrust database.
