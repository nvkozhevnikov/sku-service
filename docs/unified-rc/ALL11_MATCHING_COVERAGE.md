# All11 matching coverage — offline proposal, 16Nov2026

OPERATOR-CONFIRMED: audit all saved18959source products, correct proven matching
defects in this existing feature, replay offline and stop before decisions/mappings
are written. Base ea4455c1b4ddc36544ed8c53cad3e2fd8bc6c539,
branch feature/sterbrust-autonomous-system-20261026. Publication SHA is recorded in
workspace reports/MATCHING_ALL11_20261116/RELEASE_MANIFEST.json. No second matcher.

## SQL facts and definitions

LIVE-VERIFIED LOCAL SELECT:1282effective confirmed source→Sterbrust links represent
1226unique Sterbrust IDs. Some independent offers legitimately share a canonical
product. There are6090current REVIEW,1080CONFLICT,10503NEW_CANDIDATE and4inactive
LTT Moscow records without a current matching row. These exclusive stored buckets
sum18959. Potential FK/evidence candidates overlap REVIEW/CONFLICT/NEW and must not
be added again.25905product_matches are events, not linked product count.

Matching validation is at least as recent as the saved source success timestamp
for614of1282confirmed links;668have an older/missing validation. Neither timestamp
proves current website content. Current F01/F03 replay flags484existing links
(386KAMI/98Partner) for independent review; their Product IDs, manual authority and
history are preserved, not revoked or retargeted. No existing confirmed link becomes
eligible for an automatic replacement through this work.

The registry has59427unique rows/IDs,57405active and2022inactive. All were last
synced1Sep2026; no sterbrust_snapshot_runs audit rows exist. Model missing36915,
brand13119, article36578 across all rows. Active matching intentionally excludes
inactive targets; the audit separately searches all2022inactive rows as review leads.
Current Sterbrust completeness/absence cannot be proven from this snapshot. Private
GET credentials were not found in checked configs; operator's subsequent instruction
forbids new HTTP even if access is found. Supplier/SterbrustHTTP0.

## Proven defects and scoped fix

REPOSITORY-VERIFIED RED cases:

- A model absent from the catalog's model column but explicitly present as its own
  title model was missing from exact retrieval. Even when fuzzy retrieval found it,
  the empty comparison model prevented a supported brand+model result.
- A populated model index could hide another canonical ID whose model appeared
  only in its title, permitting an incorrect unique-candidate AUTO.
- Extending title discovery exposed a false positive: `4х-валковый` was interpreted
  as a model, mixing Sahinler4R HC10-155/HSS/4RH with10-185. This is a descriptor,
  not execution identity. Intermediate66proposals were rejected before any write.

The same RegistryContext now has an exact by_title_model retrieval index in both
PostgreSQL and CSV loaders. It unions strong scoped article/manufacturer/model/title
leads rather than stopping at the first nonempty index. A missing target model can
be projected from its visible OWN_MODEL title only; stored model and properties
are never rewritten. A nonempty contradictory model/property remains authoritative
negative evidence. Native match_product still decides unique identity with F01/F03,
brand, article namespace, execution, product-kind and characteristic guards.

Literal brand evidence can use these exact title-model leads; supplier/domain name
still cannot supply brand. A negative guard rejects numerical equipment descriptors
(roller/axis/spindle count) as positive model identity and retains pre-existing hard
CONFLICT. Legacy normalization/storage keys, NEWC/NEWG contracts and ingestion entity
identity are unchanged. Cross-supplier model matches and inactive products remain
candidate leads, never an alias or automatic canonical proof by themselves.

## Result and artifacts

REPOSITORY-VERIFIED OFFLINE: two full18959passes use the same readonly-frozen source
cards/registry/properties.61new Vekprom proposals pass native guards, with61distinct
target IDs and61/61matching full visible titles.57were storedNEW and4storedREVIEW.
All61have open review cases and no final manual veto. Their input/target hashes and
exact pairs are pinned in PROPOSED_61_LINKS_NOT_AUTHORIZED.json. This is a proposal,
not authorization or website LIVE verification. Potential source coverage changes
from1282/18959=6.76% to1343/18959=7.08% only after separately approved application.

Workspace artifacts under reports/MATCHING_ALL11_20261116/:
ACCEPTED_ALL18959.csv, ACCEPTED_NEW_SAFE_MATCHES.csv, ACCEPTED_REVIEW.csv,
ACCEPTED_CONFLICT.csv, ACCEPTED_PROBABLE_NEW.csv, ACCEPTED_BEFORE_AFTER_DELTA.csv,
ACCEPTED_EXISTING_LINK_REVALIDATION_FLAGS.csv, STERBRUST_ALL11_MATCHING_REVIEW.xlsx,
PROPOSAL_REVIEW.json, INTEGRITY_VERIFIED.json, SQL statistics and regression logs.
Only ACCEPTED_* and the final manifest are current; prior BEFORE/AFTER/FINAL_*
files are intermediate evidence. CSVs escape spreadsheet formula prefixes; IDs and
article values stay source-scoped and raw values survive the frozen snapshot.

The probable NEW list means insufficient local match evidence, not proven absence
from the current website or import readiness. It includes accessories/parts and
incomplete identities, not just machines. Missing brands and ambiguous critical
properties explain many Optimum/LTT/USS holds; no global supplier→brand substitution
or convenient duplicate-property filtering is introduced.

## Safe application boundary

DONE/APPLIED CODE: own feature only. VERIFIED: full offline replay, known false
cases, source/target/manual guards and readonly before/after hashes. Primary supplier
tables,1282original mappings, review/manual history, all11competitor/news tables and
historical117/48/206XML plus LAST KNOWN GOOD remain exact. No DB writes, collection,
matching apply, import, schedule, deployment or protected branch change.

Before any application: operator approves the exact61pair file/hash and integration
SHA; refresh readonly DB identity, original1282/manual state, source/target hashes,
active/stable IDs and null physical links. Any drift stops and requires recalculation.
Use existing native review confirmation/audit transactions, locked source rows and
CAS; do not run full-source reconcile or bypass review cases. Add only approved
relations to existing Sterbrust IDs, never create catalog products or retarget an
existing link. Verify unchanged observations/offers/news/LKG plus expected61new
audit events/relations, keep before evidence and inverse scoped review plan.
No mutation script or executable approval is supplied in this stage.

Limitations: catalog76days old; no new HTTP. Cold title-index construction adds
CPU/memory and has no LIVE latency claim; the bounded cache keeps it finite. Full
server/deployment acceptance, current catalog refresh and actual application remain
NOT_APPLIED. Stage6 import evidence and RELEASE_READY are not advanced by this work.
