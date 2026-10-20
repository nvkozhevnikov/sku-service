# Candidate evidence order — bounded correction, 2026-10-20

OPERATOR-CONFIRMED: only candidate_keys order may change. No identity, ranking,
AUTO/REVIEW/CONFLICT/NEW decision, F01/F03 guard, stored ID or schema changes.

Both SQL and offline callers use match_product. Its two ambiguous-candidate
evidence emitters now sort positive ASCII decimal IDs numerically. Original ID
strings and duplicates remain intact. Equal numeric values use their raw string
as a deterministic tie-break; non-positive/non-decimal values sort afterwards
as text. Existing best-candidate ranking uses its unchanged original comparator.

Regression covers source14865 / IDs51055 and147753, lexical/numeric differences,
leading zeros, duplicates, values beyond floating-point precision and signed64
range, zero/negative/Unicode/non-decimal inputs, both duplicate-article and
non-unique brand/model REVIEW paths. Input ordering and candidate objects are
preserved. Sixteen RED tests precede the correction; full offline regression
results and versioned artifacts are in workspace reports/RC_CANDIDATE_ORDER_PARITY_20261020_V2.

The prior offline and SQL-QA evidence remains unchanged. Read-only parity must
cover all18,958 cards against rc_verify_vi_20261019 before a PASS is reported.
No canary, matching apply, dump rebuild, migration, main/source DB writes,
push/merge/deploy/cron are authorized by this correction. STOP before SQL apply.
