# F01/F03 integration QA — conditional

Original patch:58152e4..0c0164a, reviewed by GLM with0blocking/2conditional/
4optional findings. Report source:
tmp/STERBRUST_GLM_F01_F03_REVIEW_20261019/GLM_F01_F03_VETO.md.
OPERATOR-CONFIRMED reviewer totals1635PASS/94SKIP/70subtests onPython3.14;
not rerun here or relabeled as our result.

Our REPOSITORY-VERIFIED checks onPython3.12.14Windows:
-131focusedF01+strict-key tests PASS before source integration.
-96Beka+F01+optional rendering tests PASS with socket/SQL guard denied.
-289final affected core tests PASS; no skips in that final selected set.
-Real mock detached-worker close/restart/dedup test PASS.

Two historical Beka tests found under work/sku-service/reports/RC_LOCAL and
replayed on exact copied bytes: test_saved29_scope_regression and
test_current_proposal_keeps_prior1802_and_never_confirms_alias_only. Their
scope is source identity/proposal preservation, not real DB matching apply.
SHA manifest:reports/integration/BEKA_REPLAY_INPUTS.json.

Exact original pre/post engine dry-run:12120cards/24240decisions each, fresh
and with-existing-links. With-links210auto downgrades/102promotions; fresh207/14.
Existing accepted834:126newdowngrades,326already not-auto,9different-candidate
proposals. No changes to links or canonical IDs. Manual rows0; preservation
of real manual cases cannot be inferred from absence; synthetic cases passed.
Reasons and exact rows:reports/integration/MATCHING_DRY_RUN.

Optional F3+docF5 applied with regression. F4 deferred for raw evidence
preservation. F6 suggested resolver is not equivalent for all canonical
policies; no scope expansion or policy rewrite performed.

MATCHING_GATE=NOT_VERIFIED_PENDING_LINUX_AND_CHURN_REVIEW.
Linux/3.12 not run: no running Docker Engine; no service activated. F01/F03
present provisionally in RC, with no matching apply/scheduler enable/merge.
