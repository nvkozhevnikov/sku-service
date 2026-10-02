"""Read-only NEW evidence prioritisation, never an identity decision/matcher."""
import re
from copy import deepcopy
from collections import defaultdict


def key(value):
    return re.sub(r'[^a-zа-я0-9]', '', str(value or '').lower())


def select_priority(rows, limit=30, allow_section_pending=False):
    """Require pinned absence + SAFE section; unsupported scopes stay blocked.

    Unknown required-property schemas are deliberately not treated as complete.
    A cohort membership is not a NEW or READY assignment.
    """
    selected, excluded = [], []
    peers = defaultdict(list)
    for row in rows:
        peers[key(row.get('model'))].append(row)
    for row in rows:
        if row.get('classification') != 'REVIEW':
            continue
        advisory = row.get('readiness_advisory', {})
        absence = advisory.get('CANONICAL_ABSENCE_SUPPORT', {})
        section = advisory.get('SECTION_EVIDENCE', {})
        if not (absence.get('verified') and absence.get('registry_sha256')
                and not absence.get('exact_candidate_ids') and not absence.get('plausible_family_ids')
                and (allow_section_pending or (section.get('verified') and section.get('mapping', {}).get('verdict') == 'SAFE'))):
            continue
        reasons = []
        if advisory.get('CHARACTERISTIC_CONTRADICTION', {}).get('blocked') or row.get('match_conflicts'):
            reasons.append('FACTUAL_OR_UNRESOLVED_UPSTREAM_GUARD')
        if row.get('model_role') != 'OWN_MODEL' or not row.get('brand') or not row.get('model'):
            reasons.append('SOURCE_FULL_IDENTITY_MISSING')
        if not re.search(r'[a-zA-Z]', str(row.get('model') or '')):
            reasons.append('FULL_EXECUTION_NOT_PROVEN')
        if row.get('product_kind') not in {'machine', 'lathe', 'grinder'}:
            reasons.append('PRODUCT_KIND_OR_ACCESSORY_PROOF_REQUIRED')
        item = {k: row.get(k) for k in ('source', 'external_id', 'name', 'brand', 'model',
                                       'source_url', 'evidence_ref', 'observed_properties')}
        item.update(absence=deepcopy(absence), section=deepcopy(section), diagnostic_only=True,
                    current_classification='REVIEW', expected_route='REVIEW_PENDING_STRICT_NEW_PROOF')
        if reasons:
            item['blocking_reasons'] = reasons
            excluded.append(item)
            continue
        matching_peers = [p for p in peers[key(row.get('model'))]
                          if (p.get('source'), p.get('external_id')) != (row.get('source'), row.get('external_id'))]
        item['exact_model_source_peers'] = [{k: p.get(k) for k in
                                           ('source', 'external_id', 'brand', 'classification', 'sterbrust_product_id')}
                                          for p in matching_peers]
        item['missing_gates'] = ['INDEPENDENT_TYPED_IDENTITY_ANCHORS', 'CROSS_SOURCE_DEDUP_PROOF']
        if not section.get('verified'):
            item['missing_gates'].append('SAFE_SECTION_EVIDENCE')
        item['absence_scope'] = 'PINNED_SNAPSHOT_SUPPORT_NOT_LIVE_OR_FINAL_NEW_PROOF'
        item['identity_complete'] = False
        item['expected_route'] = 'READY_TO_CREATE_IDENTITY_ONLY_IF_ALL_STRICT_GATES_PASS_ELSE_REVIEW'
        selected.append(item)
    selected.sort(key=lambda r: (-bool(r['section'].get('verified')),
                                 -len(r.get('observed_properties') or []), r['source'], r['external_id']))
    return {'priority': selected[:limit], 'eligible_count': len(selected), 'excluded': excluded,
            'input_mutated': False, 'actual_ready_promotions': 0}
