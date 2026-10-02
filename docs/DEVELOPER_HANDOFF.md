# Developer handoff: FINAL Universal Supplier RC

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

D. Deployment: **not authorized/executed here**. First obtain explicit isolated
restore-rehearsal target/create/restore/verify approval. Verify empty target,
roles,PG17.11,SHA/counts/migrations, then separately authorize application start.
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
