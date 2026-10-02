"""Terminal routing of accepted evidence, never a new matcher or identity grant."""
from collections import Counter


def closure_record(row):
    classification = row['classification']
    if classification not in {'EXISTING_CONFIRMED', 'REVIEW', 'CONFLICT'}:
        raise ValueError('Unaccepted classification in closure')
    record = {'source': row['source'], 'external_id': str(row['external_id']),
              'classification': classification, 'decision_changed': False,
              'source_url': row.get('source_url'), 'evidence_ref': row.get('evidence_ref')}
    if classification == 'EXISTING_CONFIRMED':
        pid = str(row.get('sterbrust_product_id') or '')
        if not pid.isdigit() or not row.get('full_model_confirmed'):
            raise ValueError('Existing lacks accepted real canonical identity proof')
        return {**record, 'terminal_route': 'EXISTING_CONFIRMED', 'sterbrust_product_id': pid,
                'blocking_reasons': []}
    if classification == 'CONFLICT':
        return {**record, 'terminal_route': 'CONFLICT_RETAINED',
                'blocking_reasons': ['ACCEPTED_PREDECESSOR_CONFLICT'],
                'conflict_evidence': row.get('match_conflicts') or row.get('semantic_conflicts'),
                'next_action': 'Resolve factual evidence; do not clear from a discovery-completeness change'}
    advisory = row.get('readiness_advisory') or {}
    readiness = row.get('readiness') or {}
    audit = row.get('historical_hold_resolution') or {}
    reasons = set(readiness.get('identity_blockers') or [])
    reasons.update(row.get('grouping_reasons') or [])
    reasons.update(audit.get('blocking_reasons') or [])
    contradiction = advisory.get('CHARACTERISTIC_CONTRADICTION') or {}
    reasons.update(contradiction.get('reasons') or [])
    absence = advisory.get('CANONICAL_ABSENCE_SUPPORT') or {}
    candidates = sorted(set(str(x) for x in (
        absence.get('exact_candidate_ids', []) + absence.get('plausible_family_ids', []))))
    target = str(row.get('proposed_sterbrust_id') or '')
    if target.isdigit(): candidates = sorted(set(candidates + [target]))
    if not reasons: reasons.add('CANONICAL_OR_INDEPENDENT_IDENTITY_PROOF_MISSING')
    route = ('canonical_side_evidence' if candidates or audit else 'missing_identity_section_absence_evidence')
    if row.get('detail_review_reason') or row.get('listing_identity_reason'):
        route = 'saved_source_identity_quarantine'
    if audit.get('hold_verdict') == 'FACTUAL_OR_UNPARSED_BLOCKER_REQUIRES_REVIEW':
        route = 'retained_execution_blocker'
    return {**record, 'terminal_route': 'REVIEW_MISSING_EVIDENCE', 'next_evidence_route': route,
            'blocking_reasons': sorted(reasons), 'candidate_ids_retrieval_only': candidates,
            'new_candidate_id': row.get('new_candidate_id'), 'new_group_id': row.get('new_group_id'),
            'section_verified': bool((advisory.get('SECTION_EVIDENCE') or {}).get('verified')),
            'absence_verified': bool(absence.get('verified')),
            'identity_evidence_complete': bool((advisory.get('CHARACTERISTIC_IDENTITY_SUPPORT') or {}).get('identity_evidence_complete')),
            'human_required_proven': False,
            'next_action': 'Obtain missing saved/canonical-side proof; no HTTP automatically authorized'}


def close_saved_rows(rows):
    identities = [(r['source'], str(r['external_id'])) for r in rows]
    if len(set(identities)) != len(rows):
        raise ValueError('Duplicate source identities')
    records = [closure_record(r) for r in rows]
    return {'rows': records, 'terminal_counts': dict(Counter(r['terminal_route'] for r in records)),
            'evidence_routes': dict(Counter(r.get('next_evidence_route') for r in records if r['classification']=='REVIEW')),
            'reason_counts_nonexclusive': dict(Counter(reason for r in records for reason in r['blocking_reasons'])),
            'matcher_called': False, 'canonical_decisions_changed': 0}
