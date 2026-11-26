# XML continuity / LAST KNOWN GOOD — 2026-11-16

26.11.2026. В предпросмотре пустой короткий XML поясняет, что выбранных
предложений нет, и указывает на полный XML для просмотра карточек поставщика.
Размер, дата формирования и контрольная сумма находятся в «Сведениях о
файле» или «Сведениях об образце». Время показано в формате панели.
Изменение относится к отображению. Правила отбора, состав XML и блокировка
импорта сохраняются.

OPERATOR-CONFIRMED: accumulated results must survive TTL, failed collection and
technical changes. Empty current previews alone are NOT_ACCEPTED as the system's
delivered result. Scope: existing autonomous-system feature, base
`1bcedb46078675e238ba67f5bfde5d39694b3b23`. Implementation SHA and artifact hashes
are recorded outside the commit in the release manifest. No new source HTTP,
primary SQL writes, matching apply, import, deployment or scheduler activation.

## Evidence and reconciliation

LIVE-VERIFIED LOCAL SELECT: primary55461/rc_verify_vi_20261019 retains59427 catalog
rows,18959 source products,18959 offers,23475 commercial observations and23475
captures. All1282 original supplier/external/canonical/Sterbrust ID mappings match
the prior pinned baseline exactly. Product_matches25905 are history events, not
25905 confirmed products. Job-reported COMMITTED sum3608 is not a unique-product
count. These categories must not be added together.

PACKAGED-EVIDENCE: the actual Stage5 YML has117 offers and declares2026-09-08 09:23;
the original subsequent pair dated2026-10-28T11:55:50.561472+03:00 has48 Updates and
206 NEW. Original bytes and SHA256 survive unchanged.371 cohort audit rows cover
323 unique source products;48 Updates overlap the earlier117. All historical
records resolve by stable IDs, never by title. No unresolved historical identity.

| Cohort | Current independent field evidence | Current exclusion |
|---|---|---|
|Stage5/117|61 price+stock stale;56 price+stock unknown: synthetic/non-HTTP200 latest evidence|NO_FRESH_COMMERCIAL_FIELD for117|
|Updates/48|48 price+stock stale, observations28Oct|NO_FRESH_COMMERCIAL_FIELD for48|
|NEW/206|94 prices stale/112 unknown;31 stocks stale/175 unknown; content stale206|STALE_OR_UNVERIFIED_CONTENT for206|

267/323 sources have an identity-consistent non-synthetic SQL HTTP200 receipt;
56 have none. This is saved receipt evidence, not a claim of current freshness or
an independent on-disk response revalidation. Per-record diagnostics include saved
and observed price/stock, dates, IDs, independent freshness, receipt issues, review/
quarantine reasons and last receipt-backed historical observation. No older value
is substituted into a current field. Full JSON/CSV and original files reside in
workspace `reports/XML_CONTINUITY_20261116/`; package includes their portable copies.

## Before / after

REPOSITORY-VERIFIED defect: the old publisher could move latest.json from positive
to empty when the caller supplied explanations. Immutable old files existed but
ordinary access followed the empty pointer. This is now prohibited.

The same native dual_preview publisher now:

1. Takes an exclusive cross-process lock; a crash leaves a fail-closed lock.
2. Validates both structures, counts, unique IDs, allowed fields, freshness and
   hashes. Checks the previous manifest and both previous XML hashes.
3. Compares saved counts AND source/supplier/offer/observation/capture identities,
   canonical mappings and every previously included source against current reasons.
4. Writes immutable version files through temporary files, flush/fsync and atomic
   replace. Existing manifest bytes are retained even for semantically equal JSON.
5. Publishes diagnostics separately through latest_attempt.json. A positive-to-zero
   drop in either stream retains the complete previous pair at latest.json.
6. Advances the accepted inventory after every continuity PASS, including retained
   empty attempts: later accumulated rows are protected too. BLOCKED never advances it.

Gate `NO_UNEXPLAINED_DATA_OR_EXPORT_REGRESSION` blocks unexplained disappearance,
identity remapping and saved-data loss. Explained TTL exclusions may pass this
continuity gate while current feeds and RELEASE_READY remain NOT_READY. A permitted
nonzero membership change requires per-source selection/review evidence. Legitimate
deletion/remapping needs a separately reviewed baseline revision; never edit the
baseline merely to silence a failure.

## Distinct access, no import permission

Authenticated panel `/exports/previews` exposes:

- `/{kind}/download`: latest evaluated strict preview; currently0/0, import=false.
- `/preserved/{kind}`: preserved48/206 pair, explicitly historical/revalidate.
- `/history/{sha256}`: indexed original117/48/206 bytes; hash-checked attachments.
- `/candidates/download`: all206 historical NEW identities with current reasons;
  this is an enrichment/review queue export, not permission to create products.
- `/diagnostics/download`: complete latest per-source/freshness JSON.

A BLOCKED attempt is available as diagnostics only; the current XML download
rejects it, as well as a missing/mismatched publication-status receipt. Reads bind
the status to the same immutable attempt pointer before serving any XML bytes.

Current versus historical labels and import=false response headers are mandatory.
Historical Stage5 bytes retain their original format: the surrounding status and
separate route do not rewrite the signed original. Never configure Bitrix/ESOL to
poll a history/preserved endpoint. No import-ready XML is claimed; Stage6 remains
BLOCKED_IMPORT_CONTRACT_EVIDENCE and Stage7 has no authorization.

## QA, deployment boundary and recovery

DONE: code, diagnostic routes, regression tests, documentation. APPLIED LOCAL:
new reports/xml-previews store copied from verified evidence; own loopback UI only.
VERIFIED: Linux Python3.12 offline XML/UI/security regressions and matching suites;
real artifact route checks using TestClient, with auth disabled only in that test.
Unauthorized historical/cohort routes reject in separate authenticated fixtures.
Real loopback login page responds; no new real account login/DB write is claimed.
See release manifest for exact final counts/logs and SHA. No production proof.

After integration, use the same restricted DB identity, compare canonical mappings,
test both authenticated ADMIN accounts/forced password change and verify snapshot
links, current feeds, publisher lock and all role profiles. The prior post-canary
PG17.11 dump remains unchanged; its previous Windows/Linux restore is historical
evidence, not a new restore performed for this code-only change. Preserve current
runtime/export directories; never overwrite an existing installation from this
package wholesale. Preview history is separate from the database backup.

If publish.lock remains after a crash: identify publisher PID, prove no writer,
preserve lock and artifact metadata for review, then separately recover the lock.
Do not auto-steal it or delete version/history files. latest.json is authoritative
for the preserved pair; status/LKG sidecars are diagnostic and may lag after a
crash. Keep files on one local filesystem; network-share atomicity is not verified.
History registration is an explicit single-operator migration, not a web write API.

Next commercial evidence requires a new exact approved native canary contract:
current HEAD/engine/URL scope/window, unused grant, permitted policy/rates/STOP,
stable source ID, timestamped HTTP200 non-synthetic receipt and native gate commit.
Confirm exact unit price/currency/type and independently explicit availability.
Do not infer out_of_stock from missing stock or suppress fresh price for that gap.
NEW also needs fresh content, stable identity, required enrichment and review gates.
Previously dispatched/UNKNOWN URLs must follow separately approved recovery, never
automatic retry. Existing consumed approval cannot authorize this changed engine.
Content Studio shared auth/DB integration and real monitoring schedule activation
remain separate incomplete release criteria. RELEASE_READY=NO.
