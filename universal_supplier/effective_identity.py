"""Persisted effective identity contract, independent of offer ranking.

SQL expressions are repository-owned identifiers, never operator input.
Manual evidence must be latest-final and tied to the exact physical relation.
"""
ACCEPTED_MATCH_STATUSES=frozenset({'EXACT_MATCH','HIGH_CONFIDENCE_MATCH'})


def accepted_current_mapping_sql(source_id: str, catalog_id: str, match_alias: str) -> str:
    return f"""({match_alias}.is_current AND {match_alias}.source_product_id={source_id}
      AND {match_alias}.catalog_product_id={catalog_id}
      AND {match_alias}.status IN ('EXACT_MATCH','HIGH_CONFIDENCE_MATCH'))"""


def effective_manual_mapping_sql(source_id: str, catalog_id: str) -> str:
    """Same lifecycle as review sync: POSTPONED schedules, does not revoke."""
    return f"""EXISTS (
      SELECT 1 FROM review_cases emi_rc
      JOIN LATERAL (
        SELECT decision_class,sterbrust_product_id,source_product_id
        FROM review_decisions WHERE review_case_id=emi_rc.id
          AND decision_class<>'POSTPONED'
        ORDER BY created_at DESC,id DESC LIMIT 1
      ) emi_rd ON true
      JOIN sterbrust_products emi_sb
        ON emi_sb.sterbrust_product_id=emi_rd.sterbrust_product_id
      JOIN source_products emi_sp ON emi_sp.id=emi_rc.source_product_id
      WHERE emi_sp.id={source_id} AND emi_rd.source_product_id=emi_sp.id
        AND emi_rd.decision_class='MANUAL_CONFIRMED'
        AND emi_sp.catalog_product_id={catalog_id}
        AND emi_sb.catalog_product_id=emi_sp.catalog_product_id
    )"""


def effective_identity_accepted(*, source_product_id, source_catalog_product_id,
        catalog_product_id, current_match_status, current_match_catalog_product_id,
        effective_manual_source_product_id=None, effective_manual_catalog_product_id=None):
    """Accept current match OR exact effective manual authority, fail closed."""
    if (type(source_product_id) is not int or source_product_id<=0
            or type(catalog_product_id) is not int or catalog_product_id<=0
            or source_catalog_product_id!=catalog_product_id):
        return False
    automatic=(current_match_status in ACCEPTED_MATCH_STATUSES
               and current_match_catalog_product_id==catalog_product_id)
    manual=(effective_manual_source_product_id==source_product_id
            and effective_manual_catalog_product_id==catalog_product_id)
    return automatic or manual
