# Developer handoff: FINAL Universal Supplier RC

## Final release point and gate closure

SOURCE CODE: `git@github.com:nvkozhevnikov/sku-service.git`, branch `develop`,
annotated tag **universal-supplier-rc-2026-10-02**. Checkout this exact tag, not a
floating branch. FINAL_HANDOFF_SHA is its peeled commit (`git rev-parse
universal-supplier-rc-2026-10-02^{commit}`), recorded literally in the separately
delivered final REPORT/RELEASE_GIT.json. RUNTIME_CODE_BASE_SHA is
**d2a0f0ce47667da35e5a1338b774f09035a70532**. The final commit changes documentation
only; it does not claim the old runtime SHA included the new gate-closure docs.

LIVE-VERIFIED local restore gate: final custom dump restored successfully into
new PG17.11 `127.0.0.1:55450/universal_supplier_restore_rehearsal`, system identifier
7692081906172255244, owner rehearsal_app. Empty target proven before restore;
errors0;15 migration SHAs match,109FK anti-joins PASS/broken0, all five namespaces
and4407products/offers preserved. Original universal_supplier_server and production
untouched. Evidence: separately delivered FINAL_LOCAL_VISUAL_QA_2026-10-02/
RESTORE_VERIFICATION.json. This closes local restore rehearsal, not Linux deployment.

LOCAL PANEL native startup/health/readiness passed against this restored DB.
**VISUAL_BROWSER_QA = DEFERRED_BY_OPERATOR**, not VERIFIED: browser automation was
unable to access localhost. UI/UX redesign is out of scope. This does not block
package delivery; ordinary browser smoke QA remains required after deployment.

DEPLOYMENT CHECKLIST (execution requires target-specific authorization):
1. Checkout exact annotated tag/peeled SHA.
2. Create private server .env from .env.server.example with independent secrets.
3. Provision approved PostgreSQL17.x persistent storage (first target17.11).
4. Create a new empty target DB and owner; never overwrite current/production DB.
5. Verify separately delivered dump SHA.
6. Restore with no-owner/no-privileges/exit-on-error; see DB_RESTORE.md.
7. Verify15 migrations/FKs/counts, target roles/identity and namespace counts.
8. Mount separate accepted artifacts and SHA pin, then start real application.
9. Verify health/readiness.
10. Run login/navigation/products/five namespaces/Existing/NEW/Review/Conflict/
    history/evidence/exports/browser smoke QA. Do not trigger collection/import.

SECRETS are not in Git; actual server credentials must be provisioned separately.
No automatic production merge, scheduler/worker/collection enable, Sterbrust write
or ESOL import. NEW has no Product ID; unresolved is never actionable. Stage6 stays
BLOCKED_IMPORT_CONTRACT_EVIDENCE. This is ready for developer transfer, not an
authorization or proof of production deployment.

## Problem

Transfer the completed local supplier application, five-namespace DB and accepted
decisions without publishing secrets/data to Git or implying production import.

## Business impact

4407 source records,506accepted Existing source links to458unique canonical IDs,
2complete NEW groups,3640Review,259Conflict. Unresolved records remain fail-closed.

## Current behavior

All five suppliers are consolidated/current in isolated Windows RC PG17.11.
Accepted matching/full payloads are immutable filesystem proposals, not new SQL
canonical links. Neutral XML2.0/sixCSV/QA workbook separately deliver all classes.
Canonical duplicate146998/147023 and data-error packs remain correction evidence.

## Expected behavior

Developer obtains code from GitHub **develop**, fixed SHA in external RELEASE_GIT
metadata and final REPORT. Separate dump+SHA is restored to explicitly approved
new persistent PG17.11 target, secrets supplied privately. Accepted artifact
package mounted read-only with SHA pin powers /rc-final. Scheduler/import OFF.

## What was already checked

Frozen precursor hashes/counts; all4407 source rows exactly once; two groups/no
canonical IDs for NEW; NULL request-price; full execution/role/option preservation.
Read-only backup identity/counts and custom archive inventory. Focused regression
tests and full-suite failure inventory. Workbook rendered and reopened; native
MicrosoftExcel was not tested. No Linux real restore/deployment proof exists.

## Root cause

VERIFIED: historical persisted matches/catalog counts differ from latest accepted
offline proposals. A DB restore alone does not apply those proposals. Full-suite
historical report fixtures are absent; old test expects only001–014 although015
commercial observation migration is accepted. Baseline was not modified for green.

## Required actions

A. GitHub: git@github.com:nvkozhevnikov/sku-service.git, develop, fixed commit in
RELEASE_GIT.json (outsideGit) and REPORT. Feature branches preserved. No production
merge/tag deployment.

B. Database: separate universal_supplier_rc_final.dump,82,941,740bytes,
SHA25627337c8a8e0b09924df8a2e160aa622c15e463ec8934614e6743dc2508caf8cc.
Use DB_RESTORE.md. No physical Windows PG directory delivery.

C. Secrets: populated .env.server delivered separately, never committed or printed.
Supply DB credentials/session secret independently. Existing local Windows
passfile/admin setup is not a server secret delivery.

D. Deployment: **not authorized/executed here**. Local disposable restore gate
is VERIFIED as above; new server-target provisioning/restore/start still requires
separate approval. Verify empty target,roles,PG17.11,SHA/counts/migrations.
Mount accepted artifacts at server-data/final-rc and pin manifest SHA. Verify
/health,/ready, authenticated /rc-final and DB namespace counts.

## Affected components

Adapters/passive supplier persistence, strict evidence/proposal layers, grouping/
readiness/full enrichment, neutral export, control-plane accepted release view,
server compose, logical backup/restore documentation, regression tests.

## Must not break

No fuzzy auto-identity, no alias-only confirmation, no accessory/machine mixing,
no execution/suffix/condition/axis/typed contradictions ignored. No invented ID,
article/manufacturer/docs. One source identity in one NEW group. Price request
null. Options vs base and power/weight roles intact. Reviewer candidates remain
non-actionable. Disabled suppliers/inactive offers are not activated by exports.

## Acceptance criteria

Frozen506/2/3640/259,canonical unique458,source counts1225/1353/1642/109/78.
Restore counts described in DB_RESTORE.md. Runtime depends only on DB_* and mounted
relative artifacts, not Windows paths. Scheduler/worker off,manual write off,
Sterbrust/ESOL off, no startup crawl/migration. Real rehearsal not falsely claimed.

## Verification steps

Check final manifests/dumpSHA; run scripts/verify_final_release.py on received
artifact directory, focused tests and full suite with actual legacy results.
After authorized restore run read-only SQL from DB_RESTORE.md, then health/login/
accepted-release filters/downloads. Check ordinary startup issues zero collection
requests. Do not rerun matching/discovery merely to validate the handoff.

## Rollback considerations

Preserve dump/frozen inputs/feature refs. Revert application by fixed previous SHA
only after compatibility check. Failed disposable rehearsal target is abandoned,
never restore over the source or delete a real volume. No production rollback was
needed because production is unchanged.

## Remaining unknowns

Real Linux restore/runtime,server secrets/admin access and browser QA on the new
server remain unverified. Full-suite historical fixtures need separate packaging
policy; failure details are external evidence, not fake tests. Stage6 import
contract still blocked. XML is supplier-neutral, not an ESOL/Bitrix import payload.

The workspace's requested DEVELOPER_HANDOFF_TEMPLATE.md/FINAL_REPORT_RULES.md were
not found under those exact names. Existing root templates/DEVELOPER_HANDOFF.md
and templates/FINAL_REPORT.md supplied the equivalent required sections.
