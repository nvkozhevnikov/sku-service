"""Pure, fail-closed verification helpers for scoped saved NEW evidence."""
import hashlib
import html
import json
import re
import unicodedata
from copy import deepcopy


def norm(value):
    text = unicodedata.normalize('NFKD', str(value or '').lower())
    return re.sub(r'[^a-zа-я0-9]', '', ''.join(c for c in text if not unicodedata.combining(c)))


def plain(value):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', html.unescape(str(value or '')))).strip()


def family_hits(text, tokens):
    folded = unicodedata.normalize('NFKD', plain(text).lower())
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    return [t for t in tokens if re.search(r'(?<![a-zа-я])' + re.escape(t) + r'(?![a-zа-я])', folded)]


def role(label):
    s = plain(label).lower()
    if re.search(r'(диаметр.*шкив|pulley.*diameter)', s): return 'pulley_diameter'
    if re.search(r'(ширина.*шкив|pulley.*width)', s): return 'pulley_width'
    if re.search(r'(ширина.*(полотн|ленточн.*пил|пильн.*лент)|blade.*width)', s): return 'blade_width'
    if re.search(r'(длина.*(полотн|ленточн.*пил|пильн.*лент)|blade.*length)', s): return 'blade_length'
    if re.search(r'(максимальн.*ширина.*(распил|рез)|max.*cut.*width)', s): return 'cut_width'
    if re.search(r'(максимальн.*высота.*(распил|рез)|max.*cut.*height)', s): return 'cut_height'
    return None


def geometric_value(label, value, unit=''):
    if not re.search(r'мм|\bmm\b', plain(label) + ' ' + str(unit), re.I): return None
    text = plain(value).replace(',', '.')
    text = re.sub(r'\s+', '', text)
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(?:мм|mm)?', text, re.I)
    return float(match.group(1)) if match else None


def anchors(pairs):
    result = {}
    for label, value, unit in pairs:
        r, n = role(label), geometric_value(label, value, unit)
        if r and n is not None: result.setdefault(r, set()).add(n)
    return result


def verify_anchor_proof(case, actual):
    failures = []
    for anchor in case['anchors']:
        if actual.get(anchor['role']) != {float(anchor['numeric_mm'])}:
            failures.append('VISIBLE_TYPED_ANCHOR_MISSING_OR_CONTRADICTORY:' + anchor['role'])
    independent = set(actual) - {'pulley_width'}
    if len(independent) < 2: failures.append('INDEPENDENT_TYPED_ANCHORS_MISSING')
    return failures


def readiness_gate(gates):
    required = ('SOURCE_IDENTITY', 'INDEPENDENT_TYPED_IDENTITY', 'CROSS_SOURCE_DEDUP',
                'CANONICAL_ABSENCE', 'SAFE_SECTION')
    return all(gates.get(g) == 'PASS' for g in required)


def deterministic_group_id(brand, full_model, kind='machine'):
    from .new_group_proposals import _group_id
    from sterbrust_matching.matching import Product
    from sterbrust_matching.normalization import normalize_brand
    return _group_id(Product(key='proposal', name=full_model, model=full_model,
                             brand=normalize_brand(brand), product_kind=kind))


def apply_ready_proposal(row, proof):
    """Idempotent projection of an independently verified proof; no DB access."""
    from .proposal_export import new_candidate_id
    from .new_readiness import propose_readiness
    if not readiness_gate(proof['gates']) or proof.get('failures'):
        raise ValueError('Unproven readiness gate')
    if (row['source'], row['external_id']) != (proof['source'], proof['external_id']):
        raise ValueError('Proof belongs to another source identity')
    if row['classification'] not in ('REVIEW', 'READY_TO_CREATE_IDENTITY'):
        raise ValueError('Existing/Conflict must not be rewritten')
    result = deepcopy(row)
    if 'previous_readiness_advisory' not in result:
        result['previous_readiness_advisory'] = deepcopy(result.get('readiness_advisory'))
    result.update(new_candidate_id=row.get('new_candidate_id') or new_candidate_id(row['source'], row['external_id'], norm(proof['model'])),
                  new_group_id=deterministic_group_id(proof['brand'], proof['model']),
                  classification='READY_TO_CREATE_IDENTITY', sterbrust_product_id=None, proposed_sterbrust_id=None,
                  proposed_section_id=proof['section']['id'], proposed_section_path=proof['section']['name'],
                  readiness_proof=deepcopy(proof), source_full_model_verified=True)
    advisory = {'CHARACTERISTIC_CONTRADICTION': {'blocked': False},
                'CANONICAL_ABSENCE_SUPPORT': {'verified': True, 'scope': 'pinned59500_snapshot', 'proof_ref': proof['case_id']},
                'SECTION_EVIDENCE': {'verified': True, 'assignment': proof['section']},
                'CHARACTERISTIC_IDENTITY_SUPPORT': {'identity_evidence_complete': True, 'keys': sorted(proof['typed_anchors'])}}
    result['readiness_advisory'] = advisory
    result['readiness'] = propose_readiness(dict(result, classification='NEW_CANDIDATE'), advisory,
                                          dedup_verified=True, group_id=result['new_group_id'])
    return result
