# Matching Policy V2 acceptance

## Automatic matching required — operator correction, 2026-10-19

OPERATOR-CONFIRMED: permanent MATCHING HOLD in the final system is
**NOT_ACCEPTED**. The earlier ACCEPTED_WITH_CONDITIONS proposal is superseded.
A temporary protective hold is permitted only during development, before an
explicitly approved initial application, or in response to a critical defect.

Required normal operation: shared parser queue -> existing F01/F03 matcher ->
AUTO ACCEPT for proven exact identity; REVIEW for ambiguity; CONFLICT for proven
contradiction; NEW CANDIDATE for an unlinked candidate absent identity proof in
this canonical snapshot. NEW does not create a canonical product automatically.
Preserve existing confirmed IDs, mappings and operator authority; no fuzzy
retargeting of an existing link. No switch may bypass semantic guards.

Current task permits code, regressions and a complete 18,958-card offline replay.
Bulk application to isolated RC PostgreSQL requires separate operator approval
of the exact plan. It has not been performed. Linux QA remains a separate gate;
Windows results do not close it. No live HTTP, source DB changes, ESOL,
push/merge/deploy or scheduler activation is authorized.

Evidence: reports/RC_AUTOMATIC_MATCHING_20261019 (workspace root).
Acceptance remains PENDING; a permanently disabled matcher cannot satisfy it.

## F01/F03 isolated code correction — 2026-10-19

OPERATOR-CONFIRMED: local feature only, base58152e4bd390361d3e97ee475bae78a91295e241;
no push/PR/merge/deployment/DB changes or matching apply.
REPOSITORY-VERIFIED criteria in tests/test_f01_f03_identity_inputs.py:

- Both Product constructors preserve significant owned execution for strict checks;
  broad candidate retrieval still uses legacy keys. With base/full candidates,
  the wrong execution is never automatic. Reference tails do not become own models.
- Distinct property values/units survive; critical ambiguity blocks auto in either
  row/candidate order, even without an opposite value. Equal duplicates and
  non-identity multiplicity do not require REVIEW merely because there are N rows.
- Manual canonical IDs, supplier namespaces, numeric article compatibility,
  legacy normalization and NEWC/NEWG recipes remain protected.
- RED before patch and GREEN after patch are local code evidence, not persisted
  matching or LIVE acceptance. Missing local historical evidence is an explicit SKIP.

Compatibility design/source boundaries are maintained in docs/STRICT_IDENTITY_KEYS.md.
F02/R04/R05, UI and PostgreSQL architecture remain outside this correction.

## Feature checkpoint permission — OPERATOR-CONFIRMED 2026-10-08 (`bf5d1d67`)

The verified V2.1 matching state may now be packaged: fresh exact identity/hash
preflight, focused regression freeze, new custom dump/new disposable restore DB,
unchanged business fingerprints, sanitized feature diff, feature-only commit/push.
This supersedes the earlier no-commit/no-push stop only for this feature checkpoint.
No new matching/apply, selection evaluation/apply, XML/import, NEW, supplier HTTP,
develop/production merge/ref change, release tag, deployment or scheduler permission.
Characteristics remain important corroborating/disambiguation/negative evidence;
different counts or missing values on one side are not an identity quota.

## V2.1 defect correction — OPERATOR-CONFIRMED 2026-10-08 (`2ec9a0b6`)

Preserve EXECUTION_PLUS inside own model tokens, including S+, ATC+C,6K+2,
45S+PRO and PRO+CTS30. PUNCTUATION_PLUS in ordinary prose is separately tagged.
No S+/S alias exists. V2.1 normalization is matching-only: source and registry
raw/legacy hashes are not rewritten. Search synonyms still cannot authorize links.

Same-role main-drive/main-motor power converts W/kW. Spindle, installed total,
hydraulic drive, scoring, feed, polish and duty-cycle roles remain separate.
Processing/turning diameter, above-bed/above-carriage positions remain separate.
Missing or unproven functions are not contradictions or mandatory anchor gaps.
Multiple unaligned generic motors remain uncertain, never convenient scalar picks.
Material scalar tolerance is an explicit implementation inference:1%relative or
0.01base-unit absolute round-off, whichever is larger. Full model/execution guards
remain exact; no capacity neighbour or alias is learned from numerical similarity.

Rejected V2 artifacts remain historical and immutable. Fresh>=200 Codex evidence
inspection (not independent human ground truth), all plus and contradiction cases,
35calibrations, negative/manual/feed/selection regressions and fresh DB preflight
are mandatory before the operator's conditional KAMI-only normal-lifecycle apply.
Second validation may update audit timestamps only: semantic no-op is not an
exact physical no-op. No source facts/offers/selection/predecessor mutations.

OPERATOR-CONFIRMED 2026-10-08, attachment `87df7f85`.

Compatible observed brand/manufacturer + exact full model/execution + compatible
sold-product kind + no factual contradiction is strong identity. Characteristics
are supporting, contradiction or disambiguation evidence, never a positive quota.
Missing fields and different property counts are not contradictions. Compare only
the same semantic property with compatible unit and role. Unknown duty-cycle and
dimension-vector roles must not be flattened.

Exact own variant may appear in a family/series canonical name. A true parent card
without a single explicit execution is not accepted. Weaker canonical wording may
be flagged CANONICAL_NAME_REVIEW without changing canonical names.

Identity aliases are finite, evidence-scoped and separate from retrieval-only
SEARCH_SYNONYM. EcoWood/HCM applies only to SR-RP630/1300. Exact operator-confirmed
Osama S2R1300 pair permits canonical Griggio metadata with visible Osama title,
not global Griggio/Osama equivalence. Filato Boring23 шпинделя/Boring23 and Ermaksan
PowerBend2100-40/PowerBendFalcon2100x40 are exact scoped calibrations.

No fuzzy auto acceptance, no model-only numeric/measurement keys. Suffix,
execution, accessory, condition and axis guards remain mandatory. Multiple
passing active canonical IDs => REVIEW, never arbitrary first match. No exact
candidate is not canonical absence or NEW proof.

RC apply requires all 5264 reconciled, positive/negative tests, manual lifecycle
and feed/selection tests, and a stratified >=200 Existing QA (all if fewer), with
zero confirmed false positives. Use normal matching persistence only for accepted
KAMI decisions. Do not recalculate predecessor decisions, apply selection, update
commercial facts, create NEW/canonical IDs, commit/push or touch protected branches.

## Unified readiness scope —2026-10-20 (supersedes outdated readiness assertions above)

OPERATOR-CONFIRMED requirements; implementation proof is local code/offline/read-only only.

| Criterion | Acceptance / status | Scope and evidence |
|---|---|---|
| Automatic matching | REQUIRED; permanent HOLD NOT_ACCEPTED | F01/F03 retained; proven exact AUTO, REVIEW ambiguity, CONFLICT contradiction, NEW proposal only. Preserve confirmed IDs/no fuzzy retarget; never bypass guards to inflate AUTO |
| REVIEW operator work | Exception for missing/ambiguous proof, not all cards | Reasons/evidence remain visible; not force AUTO without proof |
| One panel/one PostgreSQL | REQUIRED | Existing create_app, queue/schema; no second app/newDB/migration |
| HTTP success≠SQL commit | REQUIRED; code/offline verified | Missing measurements unknown; SQL failure shown; no matching receipt not evidence of never run |
| Strict presence/quality idempotency | REQUIRED; code fixed, SQL re-QA PENDING | All-array SEEN dedup, run row lock; old duplicates preserved; current TEST_AFTER baseline |
| Existing9wrapper20s floor | ACCEPTED_WITH_CONDITIONS temporary only | One sequential worker, after-response pacing≥20s; not proven universal site requirement |
| Universal irreversible20s final policy | NOT_ACCEPTED | Per-host measured/approved profiles required. Prepared shared start limiter/pool not activated |
| Full fresh11sources | REQUIRED; NOT_VERIFIED | Discovery+refresh+durable resume+actual SQL/history terminal receipts; manifest refresh insufficient, partial must not deactivate |
| New host rate profiles | Separate authorization before LIVE | Explicit exact host-policy digest + robots/Crawl-delay/Retry-After/lease required; stop403/429/interstitial |
| Linux Python3.12 | REQUIRED before final acceptance; NOT_RUN | Windows tests cannot close; no user Docker/process launch |
| Stage6 import / ESOL | BLOCKED_IMPORT_CONTRACT_EVIDENCE | No production mutation from green tests or artifact validity |

Latest evidence: workspace reports/RC_UNIFIED_READINESS_CODE_20261020. No SQL write, canary, cleanup, restore, new HTTP, scheduler activation, push/merge/deploy authorized by this code report.
