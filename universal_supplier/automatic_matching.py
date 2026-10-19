"""Safe automatic planning shared by queue execution and offline RC replay.

The F01/F03 matcher is the sole positive identity authority. Planning never
changes a card or registry; existing accepted mappings are never retargeted.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from functools import lru_cache

from sterbrust_matching.matching import match_product
from sterbrust_matching.product_identity import classify_model_role, REFERENCE_MODEL
from .matching_service import RegistryContext, _candidate, _source, match_cards

POLICY_VERSION = "automatic-f01-f03-preserve-v1"
BUCKETS = ("AUTO", "REVIEW", "CONFLICT", "NEW", "NOT_PROCESSED")


@lru_cache(maxsize=4)
def _brand_patterns(brands):
    return [(brand, re.compile(r'(?<![a-zа-я0-9])'+re.escape(brand)+r'(?![a-zа-я0-9])'))
            for brand in brands if brand]


def hydrate_card(card, registry=None):
    """Expose retained parser observations to matching, without editing storage.

    Commercial writers intentionally retain technical facts in source_content,
    whereas the old SQL loader only read normalized property tables.
    Both representations are observations; preserve every distinct value.
    """
    from .models import PropertyValue, CategoryNode
    content = card.raw_data.get("source_content") or {}
    properties = list(card.properties)
    # The raw pair is the same observation as its normalized table row. Its
    # missing separate unit does not create a second, unitless measurement.
    seen = {(p.name, p.value) for p in properties}
    for item in content.get("technical_properties") or ():
        if isinstance(item, (list, tuple)) and len(item) == 2:
            name, value = map(str, item)
            if (name, value) not in seen:
                declared_unit = re.search(r"[,\(]\s*(мм|см|м|квт|вт|в|кг|mm|cm|m|kw|w|v|kg)\s*\)?$", name, re.I)
                unit = declared_unit.group(1) if declared_unit else ""
                properties.append(PropertyValue(name, value, unit, len(properties)))
                seen.add((name, value))
    categories = card.categories
    if not categories and content.get("source_category"):
        categories = (CategoryNode(str(content["source_category"]), str(content.get("source_category_url") or ""), 0),)
    # Explicit saved brand fields are evidence; supplier name and fuzzy title
    # similarity are not a substitute for a manufacturer/brand observation.
    identity = card.raw_data.get("source_identity") or {}
    record_identity = (card.raw_data.get("source_record") or {}).get("identity") or {}
    brand = card.brand or identity.get("brand") or record_identity.get("brand") or ""
    brand_values = {p.value.strip() for p in properties if p.name.strip().casefold() in {"brand", "бренд"} and p.value.strip()}
    if not brand and len(brand_values) == 1:
        brand = next(iter(brand_values))
    if not brand and registry is not None:
        # A literal brand printed on this card can corroborate an exact model
        # or explicit manufacturer article retrieved from the catalogue. A
        # supplier/domain name, absent brand, or fuzzy candidate cannot do so.
        from sterbrust_matching.normalization import normalize_brand, normalize_model, normalize_text, article_lookup_key
        source = _source(replace(card, properties=tuple(properties), categories=categories))
        printed = {}
        title = normalize_text(card.name)
        for normalized, pattern in _brand_patterns(tuple(sorted(registry.by_brand))):
            if not pattern.search(title):
                continue
            ids = set(registry.by_model.get(normalize_model(source.model, normalized), []))
            if source.manufacturer_article:
                ids.update(registry.manufacturer_identifier_index.get(article_lookup_key(source.manufacturer_article), []))
            for key in ids:
                candidate_brand = registry.products[key]['brand']
                if normalize_brand(candidate_brand) == normalized:
                    printed[normalized] = candidate_brand
        if len(printed) == 1:
            brand = next(iter(printed.values()))
    return replace(card, brand=str(brand), properties=tuple(properties), categories=categories)


def plan_card(card, registry: RegistryContext, existing=None, *, active=True,
              stable_identity=True):
    """Revalidate one existing target directly; never fuzzy-rematch its identity."""
    existing = existing or {}
    target = str(existing.get("sterbrust_product_id") or "")
    base = {"supplier_code": card.supplier_code, "source_external_id": card.external_id,
            "policy_version": POLICY_VERSION, "preserved_sterbrust_product_id": target,
            "write_action": "NONE"}
    if not active:
        return {**base, "bucket": "NOT_PROCESSED", "reason": "INACTIVE_SOURCE"}
    if not stable_identity:
        return {**base, "bucket": "REVIEW", "reason": "UNSTABLE_SOURCE_IDENTITY"}
    card = hydrate_card(card, registry)
    if target and target not in registry.products:
        return {**base, "bucket": "REVIEW", "reason": "EXISTING_TARGET_NOT_IN_ACTIVE_REGISTRY"}
    if target:
        result = match_product(_source(card), [], existing_link=_candidate(registry.products[target]),
            existing_link_origin=existing.get("origin_match_method", ""),
            existing_link_manual=bool(existing.get("manual_or_human_confirmed")))
        # A manual confirmation is retained as operator authority, not newly
        # auto-accepted; contradictory observations remain visible in a direct
        # semantic check without changing the operator's mapping.
        if existing.get("manual_or_human_confirmed"):
            check = match_product(_source(card), [], existing_link=_candidate(registry.products[target]),
                                 existing_link_origin=existing.get("origin_match_method", ""))
            if not check.auto_accepted:
                result = check
        decision = {"source_external_id": card.external_id, "sterbrust_product_id": target,
                    "status": result.status, "match_method": result.method,
                    "auto_accepted": str(result.auto_accepted).upper(),
                    "confidence_score": result.confidence, "evidence": json.dumps(result.evidence, ensure_ascii=False),
                    "conflicts": json.dumps(result.conflicts, ensure_ascii=False),
                    "warnings": json.dumps(result.warnings, ensure_ascii=False)}
    else:
        decision = match_cards([card], registry)[0]
    source = _source(card)
    from sterbrust_matching.normalization import normalize_brand
    identity = card.raw_data.get("source_identity") or {}
    record = (card.raw_data.get("source_record") or {}).get("identity") or {}
    observed_brands = {normalize_brand(v) for v in (
        card.brand, identity.get("brand"), record.get("brand"), record.get("brand_raw"),
        *(p.value for p in card.properties if p.name.strip().casefold() in {"brand", "бренд"})
    ) if v and normalize_brand(v)}
    if len(observed_brands) > 1:
        decision = {**decision, "status": "REVIEW", "match_method": "AMBIGUOUS_SAVED_BRAND",
                    "auto_accepted": "FALSE"}
    conflicts = json.loads(decision.get("conflicts") or "{}")
    if decision["status"] == "CONFLICT" and conflicts and all(
        isinstance(v, dict) and v.get("result") == "PROPERTY_AMBIGUOUS_DUPLICATE_VALUES"
        for v in conflicts.values()
    ):
        evidence = json.loads(decision.get("evidence") or "{}")
        evidence["underlying_matcher_decision"] = {"status": decision["status"], "method": decision["match_method"]}
        decision = {**decision, "status": "REVIEW", "match_method": "AMBIGUOUS_CRITICAL_OBSERVATIONS",
                    "evidence": json.dumps(evidence, ensure_ascii=False)}
    if (decision.get("auto_accepted") in (True, "TRUE")
            and classify_model_role(source.name, source.category, source.model).role == REFERENCE_MODEL):
        decision = {**decision, "status": "REVIEW", "match_method": "REFERENCE_MODEL_ONLY",
                    "auto_accepted": "FALSE"}
    auto = decision.get("auto_accepted") in (True, "TRUE")
    bucket = "AUTO" if auto or (target and decision["match_method"] == "EXISTING_LINK_MANUAL") else (
        "CONFLICT" if decision["status"] == "CONFLICT" else
        "NEW" if decision["status"] == "NEW_CANDIDATE" else "REVIEW")
    if bucket == "NEW" and not _source(card).model:
        bucket = "REVIEW"
        decision = {**decision, "status": "REVIEW", "match_method": "INSUFFICIENT_OWN_IDENTITY"}
    elif bucket == "NEW" and not source.brand:
        # Retrieval may have found the same model but no brand proof. That is
        # missing identity evidence, not evidence of absence from the catalogue.
        from sterbrust_matching.normalization import normalize_model
        if registry.by_model.get(normalize_model(source.model)):
            bucket = "REVIEW"
            decision = {**decision, "status": "REVIEW", "match_method": "MISSING_BRAND_FOR_MODEL_CANDIDATE"}
    return {**base, "bucket": bucket, "reason": decision["match_method"],
            "decision": decision, "write_action": "PRESERVE_EXISTING" if target else
            "ACCEPT_EXACT" if bucket == "AUTO" else "RECORD_PROPOSAL"}


def summarize(plans):
    sources = {}
    reasons = {}
    for row in plans:
        code = row["supplier_code"]
        counts = sources.setdefault(code, dict.fromkeys(BUCKETS, 0))
        counts[row["bucket"]] += 1
        reasons.setdefault(code, Counter())[row["bucket"] + ":" + row["reason"]] += 1
    return {"policy_version": POLICY_VERSION, "total": sum(sum(v.values()) for v in sources.values()),
            "by_source": sources, "reasons": {k: dict(v) for k, v in reasons.items()}}


def runtime_matching_policy(config=None):
    """RC initial writes require a separately approved operator launch, not a UI bypass."""
    import os
    pinned_rc = config is not None and config.host == "127.0.0.1" and config.port == 55461 and config.dbname == "sterbrust_unified_rc_20261019"
    rc = pinned_rc or os.environ.get("STERBRUST_UNIFIED_RC") == "YES"
    apply = not rc or os.environ.get("STERBRUST_RC_MATCHING_APPLY_APPROVED") == "YES"
    return {"policy_version": POLICY_VERSION, "automatic_matching": True,
            "mode": "SAFE_AUTOMATIC" if apply else "DRY_RUN_PENDING_INITIAL_APPROVAL",
            "apply_allowed": apply, "existing_ids_preserved": True,
            "f01_f03_enforced": True, "review_conflict_auto_accept": False}


def run_post_ingest(config, supplier_code, external_ids=None):
    """Normal queue hook. RC preview is a temporary initial-application gate only."""
    if config is None:
        return {"skipped": "NO_DATABASE_CONFIG", "matching_applied": False}
    from .postgres import PostgresRepository
    from .identity_service import _load_cards
    from .matching_service import load_registry_from_postgres
    policy = runtime_matching_policy(config)
    repository = PostgresRepository(config)
    try:
        registry = load_registry_from_postgres(repository.connection)
        cards, source_ids = _load_cards(repository.connection, supplier_code)
        contexts = repository.load_existing_link_contexts(supplier_code)
        # Preserve even legacy stored links whose acceptance provenance is not
        # current. A missing context must never make a stored ID replaceable.
        with repository.connection.cursor() as q:
            q.execute("""SELECT sp.external_id,sb.sterbrust_product_id
                         FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                         JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
                         WHERE s.code=%s""", (supplier_code,))
            for external, target in q.fetchall():
                contexts.setdefault(str(external), {"sterbrust_product_id": str(target)})
            q.execute("""SELECT sp.external_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                         WHERE s.code=%s AND NOT sp.external_id_is_stable""", (supplier_code,))
            unstable = {str(r[0]) for r in q.fetchall()}
        selected = None if external_ids is None else set(map(str, external_ids))
        selected_cards = [c for c in cards if selected is None or c.external_id in selected]
        plans = [plan_card(c, registry, contexts.get(c.external_id),
                           stable_identity=c.external_id not in unstable)
                 for c in selected_cards]
        persisted = Counter()
        affected = []
        if policy["apply_allowed"]:
            from datetime import datetime, timezone
            at = datetime.now(timezone.utc).isoformat()
            for card, plan in zip(selected_cards, plans, strict=True):
                if plan["write_action"] in {"ACCEPT_EXACT", "RECORD_PROPOSAL"} and "decision" in plan:
                    with repository.connection.transaction():
                        result = repository.persist_match(plan["decision"], at, supplier_code,
                                                          preserve_existing_links=True)
                        persisted[result] += 1
                        if result != "PRESERVED_EXISTING_MAPPING":
                            source_id = source_ids[(supplier_code, card.external_id)]
                            _materialize_decision(repository.connection, source_id, hydrate_card(card, registry))
                            affected.append(source_id)
        review = {}
        if affected:
            from .control_plane.admin_store import PostgresAdminStore
            review = PostgresAdminStore(config).generate_review_cases(supplier_code, source_ids=affected)
        return {"status": "succeeded", **policy, **summarize(plans),
                "persistence": dict(persisted), "review_sync": review,
                "matching_applied": any(k != "PRESERVED_EXISTING_MAPPING" for k in persisted)}
    finally:
        repository.close()


def _materialize_decision(connection, source_id, card):
    """Display the actual guarded persisted result, never the pre-lock proposal.

    Called in the same transaction as persist_match; existing mappings have
    already short-circuited and do not reach this path. No offer selection,
    canonical-product creation, manual decision or history deletion occurs.
    """
    from sterbrust_matching.product_identity import classify_product_kind
    source = _source(card)
    role = classify_model_role(source.name, source.category, source.model)
    kind = classify_product_kind(source.name, source.category).product_kind
    with connection.cursor() as q:
        q.execute("""SELECT pm.status,pm.auto_accepted,pm.match_method,sb.sterbrust_product_id,
                            pm.conflicts,pm.evidence
                     FROM product_matches pm LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=pm.catalog_product_id
                     WHERE pm.source_product_id=%s AND pm.is_current""", (source_id,))
        status, auto, method, target, conflicts, evidence = q.fetchone()
        decision = ("HIGH_CONFIDENCE_EXISTING" if status == "HIGH_CONFIDENCE_MATCH" else "EXACT_EXISTING") if auto else (
            "CONFLICT" if status == "CONFLICT" else "SAFE_NEW_PRODUCT_CANDIDATE" if status == "NEW_CANDIDATE" else "REVIEW_EXISTING")
        chars = json.dumps({"category":source.category,"identity_critical_properties":source.properties,
                            "candidate_sterbrust_ids":[str(target)] if target else [],
                            "matching_evidence":evidence}, ensure_ascii=False)
        q.execute("""INSERT INTO product_identity_decisions
                     (source_product_id,decision,own_model,reference_model,product_kind,best_sterbrust_id,
                      decision_reason,conflicts,identity_characteristics,rule_version,audited_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now())
                     ON CONFLICT(source_product_id) DO UPDATE SET
                       decision=excluded.decision,own_model=excluded.own_model,reference_model=excluded.reference_model,
                       product_kind=excluded.product_kind,best_sterbrust_id=excluded.best_sterbrust_id,
                       decision_reason=excluded.decision_reason,conflicts=excluded.conflicts,
                       identity_characteristics=excluded.identity_characteristics,rule_version=excluded.rule_version,
                       audited_at=excluded.audited_at
                     WHERE (product_identity_decisions.decision,product_identity_decisions.own_model,
                            product_identity_decisions.reference_model,product_identity_decisions.product_kind,
                            product_identity_decisions.best_sterbrust_id,product_identity_decisions.decision_reason,
                            product_identity_decisions.conflicts,product_identity_decisions.identity_characteristics,
                            product_identity_decisions.rule_version) IS DISTINCT FROM
                           (excluded.decision,excluded.own_model,excluded.reference_model,excluded.product_kind,
                            excluded.best_sterbrust_id,excluded.decision_reason,excluded.conflicts,
                            excluded.identity_characteristics,excluded.rule_version)""",
                  (source_id,decision,role.own_model or None,role.reference_model or None,kind,target,
                   method,json.dumps(conflicts,ensure_ascii=False),chars,POLICY_VERSION))
