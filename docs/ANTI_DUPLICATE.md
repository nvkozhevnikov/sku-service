# Anti-duplicate controls

The full REST registry is audited using same brand+model, same brand+normalized supplier article, brand+manufacturer article, exact normalized name in a compatible category, and blocked high name similarity. The final report contains 29,177 candidate pairs. Every row is marked `EXISTING_DUPLICATE_CANDIDATE`; no card was merged or modified.

Mandatory behavior tests cover exact Stalex model formatting, significant suffix differences, same supplier article with different brand, high name similarity with different model, two supplier offers attached to one canonical product, and the fuzzy-name-only prohibition.

Supplier article property 1273 is present in the live schema but has zero populated values in the current registry. The report therefore does not claim supplier-article evidence where the API returned none; other real identity fields remain available.

Stage 3A.3 does not weaken these controls. Existing links precede candidate discovery but are not blindly trusted: a real identity change becomes `CONFLICT`, while stale or formatting-only evidence remains an auditable warning. Duplicate supplier-specific articles still produce `REVIEW`, fuzzy name alone still cannot auto-match, and meaningful model suffix/execution tokens remain distinct.

The persistent mapping QA replays every auto-accepted result through an existing link. An unchanged mapping must remain accepted, and `RUN2_FALSE_EXISTING_LINK_CONFLICTS` must be zero. A separate changed-identity fixture proves that stability does not suppress a genuine MR-X3/MR-X4 conflict.

Cross-supplier equality of raw article text is never identity evidence. Only the identifier collection for the source supplier's exact namespace can trigger supplier-article matching; another supplier's collection and the legacy unnamespaced scalar remain candidate data only.
