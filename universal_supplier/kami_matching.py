"""KAMI-only proposal layer; immutable predecessor decisions are not re-run.

Retrieval is not acceptance. No exact model is not canonical absence evidence.
"""
from collections import defaultdict
from copy import deepcopy

from sterbrust_matching.product_identity import classify_model_role, classify_product_kind
from .beka_canonical_offline import evaluate_row


def source_view(card, evidence_ref):
    if card.supplier_code != 'kami':
        raise ValueError('KAMI-only matching scope')
    model = card.raw_data.get('full_model', '')
    category = card.categories[0].name if card.categories else ''
    evidence = card.raw_data.get('property_evidence', [])
    observed = [{'name': p.name, 'value': p.value, 'unit': p.unit,
                 'position': p.position, 'role': evidence[i].get('role', '') if i < len(evidence) else '',
                 'provenance': evidence_ref} for i, p in enumerate(card.properties)]
    values = defaultdict(list)
    for p in observed:
        values[p['name']].append(p)
    # Never choose the last convenient spindle/turret/net/gross value. Typed
    # role reconciliation is required before auto-confirming repeated labels.
    role_holds = [name for name, group in values.items()
                  if len({(p['value'], p['role']) for p in group}) > 1]
    return {'source': 'kami', 'external_id': card.external_id, 'name': card.name,
        'brand': card.brand, 'model': model, 'execution': model,
        'model_role': classify_model_role(card.name, category, model).role,
        'product_kind': classify_product_kind(card.name, category).product_kind,
        'source_category': category, 'source_url': card.canonical_url,
        'classification': 'REVIEW', 'match_status': 'HIGH_CONFIDENCE_MATCH',
        'observed_properties': observed,
        'properties': {name: group[0]['value'] for name, group in values.items() if len(group) == 1},
        'evidence_ref': evidence_ref, 'typed_role_holds': sorted(role_holds)}


def propose_card(card, evidence_ref, index):
    row = source_view(card, evidence_ref)
    reasons = []
    if card.raw_data.get('card_kind') != 'SINGLE_PRODUCT':
        reasons.append('EXPLICIT_SINGLE_EXECUTION_IDENTITY_REQUIRED')
    if not row['model']:
        reasons.append('VISIBLE_FULL_MODEL_MISSING')
    if not row['brand']:
        reasons.append('OBSERVED_BRAND_MISSING')
    if row['typed_role_holds']:
        reasons.append('TYPED_CHARACTERISTIC_ROLE_RECONCILIATION_REQUIRED')
    audit = evaluate_row(row, index) if not reasons else {'confirmed': None, 'blocking_reasons': reasons,
                                                       'candidates': [], 'retrieval': {}}
    chosen = audit['confirmed']
    if chosen:
        agreements = chosen['evidence']['typed_characteristics']['agreements']
        independent_source = {p['source']['property_name'] for p in agreements}
        independent_target = {p['canonical']['property_name'] for p in agreements}
        audit['independent_observed_anchor_fields'] = {
            'source': sorted(independent_source), 'canonical': sorted(independent_target)}
        # Length/width/thickness derived from one blade-dimensions field remain
        # one observed anchor, not three independent proofs.
        if len(independent_source) < 2 or len(independent_target) < 2:
            audit['blocking_reasons'] = ['TWO_INDEPENDENT_OBSERVED_TYPED_FIELDS_REQUIRED']
            audit['confirmed'] = chosen = None
    row.update(classification='EXISTING_CONFIRMED' if chosen else 'REVIEW',
        sterbrust_product_id=chosen['sterbrust_product_id'] if chosen else '',
        sterbrust_name=chosen['name'] if chosen else '', full_model_confirmed=bool(chosen),
        kami_matching_evidence=audit, readiness={'identity_ready': False, 'full_ready': False},
        price=str(card.price) if card.price is not None else None,
        price_state=card.raw_data['price_state'], price_basis=card.raw_data['price_basis'],
        currency=card.currency, availability=card.availability_normalized,
        canonical_absence_verified=False)
    return row


def attach_observation(row, capture, *, source_product_id=None, offer_id=None):
    """Dated capture provenance, not an inferred stock/price role."""
    result = deepcopy(row)
    result.update(observed_at=capture['observed_at'], capture_sha256=capture['sha256'],
                  source_product_id=source_product_id, offer_id=offer_id,
                  supplier_enabled=False, offer_active=False,
                  price_role='supplier_public_not_sterbrust_sale')
    return result


def merge_preserving_predecessor(predecessor, kami_rows):
    if any(r.get('source') == 'kami' for r in predecessor):
        raise ValueError('Frozen predecessor unexpectedly contains KAMI')
    if any(r.get('source') != 'kami' for r in kami_rows):
        raise ValueError('Cross-namespace mutation forbidden')
    keys = [(r['source'], str(r['external_id'])) for r in predecessor + kami_rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate supplier-scoped identity')
    return deepcopy(predecessor) + deepcopy(kami_rows)
