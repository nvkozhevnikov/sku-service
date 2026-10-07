# KAMI final operation release / 2026-10-07

OPERATOR-CONFIRMED dcc9e035. Two separate physical XML are mandatory:
NEW_PRODUCTS.xml and PRICE_UPDATES.xml, including valid zero-item files.
Both use repository neutral/2.0 envelope with operation-split/1.0 profile,
deterministic UTF-8, sorted identities, parser readback and byte-identical replay.
This is READY_ARTIFACT / NOT_IMPORT_AUTHORIZED, NOT an ESOL field mapping.
ESOL live import schema remains BLOCKED_PENDING_EVIDENCE.

USER-FACING RELEASE_SCOPE = KAMI_ONLY_DELTA. Predecessor suppliers and
aggregate canonical feed are INTERNAL/QA evidence; never used to fill a zero
KAMI bucket. Every scoped exclusion is recorded. No hidden matching/reselection.

Operational truth: persisted effective accepted identity + canonical persisted
selection. Advisory MATCHING_ACCEPTED/readiness/proposal28 are QA only and
cannot quarantine links, mutate selection or assign a new canonical ID.
The superseded 32-row/advisory assembler is disabled, not a current receipt.
Current receipt/hash guards are checked against fresh SQL before assembly.
Canonical/advisory summaries and historical selection discrepancy stay visible.

NEW requires explicit accepted FULL proof, positive absence/identity/typed/
section/dedup/machine-kind gates and complete own payload, no existing physical
link or Sterbrust ID. No NEW is inferred from missing exact/fuzzy model.
PRICE requires effective canonical identity, exact eligible persisted winner,
positive numeric exact price, supported currency/role, and comparable pinned
authoritative current Sterbrust price. Unchanged/POR/missing/unknown current
price means no update. Currency/price_type are metadata only. Price XML never
contains availability, quantity or content changes. Source/current price
comparison provenance remains explicit. Supplier HTTP0. Sterbrust GET only if
needed for actual viable scoped price comparison; no write/import authority.

Canonical apply: existing evaluate_all/apply UPSERT, no DELETE; exact first delta
captured, second changed0, unrelated tables/sequences unchanged. Old recovered
121/117/4 state is a preflight baseline, not a hardcoded future selection target.

Internal preview retains existing commercial guards. An unknown supplier price
role must not be invented as retail/Sterbrust sale price; exclusion is truthful
even if the canonical selector retains that source relationship. Neutral
artifact validation does not prove ESOL import readiness.

Final versioned custom dump only after current correctness gates PASS and all
relevant non-pass disposition. Restore into a new isolated DB, compare all
tables/sequences/FKs/migrations and regenerate both operation bytes/hashes.
Feature-only commit/push after all gates; protected branches unchanged. Visual
redesign DEFERRED_BY_OPERATOR; no browser visual verification is claimed.
