"""Pure six-namespace overlap diagnostics. Never assigns IDs from aliases/models."""
from collections import defaultdict

from sterbrust_matching.matching import Product, classify_identity_quality
from sterbrust_matching.normalization import normalize_brand, normalize_model
from sterbrust_matching.product_identity import OWN_MODEL, classify_model_role, classify_product_kind
from .execution_evidence import axis_execution_guard, condition_execution_guard
from .offline_review_resolution import compare_typed, clean_observed_model, source_title_model


def full_key(row):
    brand = normalize_brand(row.get('brand'))
    model = normalize_model(clean_observed_model(row)['model'], brand)
    return (brand, model) if brand and model else None


def equivalence(left, right):
    """Positive source-to-source proof, no fabricated canonical Product ID."""
    reasons = []
    if full_key(left) != full_key(right) or not full_key(left):
        reasons.append('BRAND_FULL_EXECUTION_NOT_EXACT')
    for row in (left, right):
        brand, model = full_key(row) or ('', '')
        if (row.get('model_role') != OWN_MODEL or
                classify_model_role(row.get('name', ''), row.get('source_category', ''), row.get('model', '')).role != OWN_MODEL or
                normalize_model(source_title_model(row), brand) != model):
            reasons.append('VISIBLE_OWN_FULL_MODEL_NOT_PROVEN')
        if (row.get('classification') == 'CONFLICT' or row.get('match_conflicts')
                or row.get('semantic_conflicts') or row.get('typed_role_holds')
                or row.get('detail_review_reason') or row.get('listing_identity_reason')):
            reasons.append('SOURCE_BLOCKER_RETAINED')
        if not row.get('evidence_ref'):
            reasons.append('SOURCE_PROVENANCE_MISSING')
    kinds = [classify_product_kind(r.get('name', ''), r.get('source_category', '')).product_kind for r in (left, right)]
    if kinds[0] != kinds[1] or kinds[0] in {'unknown', 'accessory', 'jaw', 'handle', 'controller'}:
        reasons.append('PRODUCT_KIND_NOT_EXACT')
    products = [Product(str(i), r.get('name', ''), normalize_brand(r.get('brand')),
                        clean_observed_model(r)['model'], category=r.get('source_category', ''))
                for i, r in enumerate((left, right))]
    semantic = []
    for a, b in (products, products[::-1]):
        quality, proof, conflicts, warnings = classify_identity_quality(a, b)
        semantic.append({'quality': quality, 'evidence': proof, 'conflicts': conflicts, 'warnings': warnings})
        if quality or conflicts or warnings:
            reasons.append('SEMANTIC_GUARD_BLOCKED')
    role_proofs = {}
    for label, guard in [('axis', axis_execution_guard), ('condition', condition_execution_guard)]:
        proof = guard(left.get('name', ''), left.get('properties', {}),
                      right.get('name', ''), right.get('properties', {}))
        role_proofs[label] = proof
        if proof['blocked']:
            reasons.append(label.upper() + '_EXECUTION_GUARD_BLOCKED')
    # Normalize old source properties without changing the authoritative row.
    reference = {**right, 'observed_properties': right.get('observed_properties') or
                 [{'name': k, 'value': v} for k, v in right.get('properties', {}).items()]}
    typed = compare_typed(left, reference)
    if typed['contradictions']:
        reasons.append('TYPED_CHARACTERISTIC_CONTRADICTION')
    source_fields = {p['source']['property_name'] for p in typed['agreements']}
    target_fields = {p['canonical']['property_name'] for p in typed['agreements']}
    if not typed['independent_identity_support'] or min(len(source_fields), len(target_fields)) < 2:
        reasons.append('TWO_INDEPENDENT_TYPED_FIELDS_REQUIRED')
    return {'status': 'PROVEN_SOURCE_EQUIVALENCE' if not reasons else 'RETRIEVAL_ONLY_REVIEW',
            'blocking_reasons': sorted(set(reasons)), 'semantic': semantic,
            'typed_characteristics': typed, **role_proofs,
            'canonical_assignment_performed': False}


def cross_supplier_overlaps(kami_rows, predecessor):
    index = defaultdict(list)
    for row in predecessor:
        if full_key(row):
            index[full_key(row)].append(row)
    result = []
    for row in kami_rows:
        for other in index.get(full_key(row), []):
            result.append({'kami_external_id': row['external_id'],
                'other_source': other['source'], 'other_external_id': str(other['external_id']),
                'other_classification': other['classification'],
                'retained_other_new_group_id': other.get('new_group_id'),
                'proof': equivalence(row, other)})
    return result
