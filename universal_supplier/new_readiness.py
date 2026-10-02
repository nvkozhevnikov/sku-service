"""Supplier-neutral NEW readiness proposal. No matching/DB/import mutation."""


def propose_readiness(record: dict, advisory: dict, *, dedup_verified: bool,
                      group_id: str | None, enrichment: dict | None = None) -> dict:
    """Identity and content completeness are separate fail-closed gates.

    Caller must supply evidence-backed dedup outcome, not model-only grouping.
    Existing/Conflict classifications are never rewritten by this component.
    """
    classification = record.get('classification')
    if classification not in ('NEW_CANDIDATE', 'REVIEW'):
        return {'state':classification, 'diagnostic_only':True, 'input_mutated':False}
    blockers=[]
    contradiction=advisory.get('CHARACTERISTIC_CONTRADICTION', {})
    if classification == 'REVIEW' or contradiction.get('blocked'):
        blockers.append('REVIEW_OR_SEMANTIC_CONTRADICTION')
    if not record.get('new_candidate_id'): blockers.append('NEW_CANDIDATE_ID_MISSING')
    if not dedup_verified or not group_id: blockers.append('CROSS_SOURCE_DEDUP_UNVERIFIED')
    if not advisory.get('CANONICAL_ABSENCE_SUPPORT', {}).get('verified'):
        blockers.append('NEW_NEEDS_ABSENCE_PROOF')
    if not advisory.get('SECTION_EVIDENCE', {}).get('verified'):
        blockers.append('NEW_NEEDS_SECTION')
    if not advisory.get('CHARACTERISTIC_IDENTITY_SUPPORT', {}).get('identity_evidence_complete'):
        blockers.append('NEW_NEEDS_IDENTITY_EVIDENCE')
    identity_ready=not blockers
    content_blockers=[]
    proof=(enrichment or {}).get('enrichment_evidence',{})
    for field in ('description','characteristics','images','documents'):
        if not proof.get(field+'_checked'): content_blockers.append(field.upper()+'_NOT_CHECKED')
    for field in ('description_text','technical_properties','source_images'):
        if not (enrichment or {}).get(field): content_blockers.append(field.upper()+'_MISSING')
    if not proof.get('source_url') or not (enrichment or {}).get('evidence_ref'):
        content_blockers.append('DETAIL_PROVENANCE_INCOMPLETE')
    if not (enrichment or {}).get('price_state'): content_blockers.append('PRICE_STATE_NOT_OBSERVED')
    if 'availability' not in (enrichment or {}): content_blockers.append('AVAILABILITY_NOT_OBSERVED')
    full_ready=identity_ready and not content_blockers
    state=('READY_TO_CREATE_FULL' if full_ready else 'READY_TO_CREATE_IDENTITY' if identity_ready
           else 'REVIEW' if 'REVIEW_OR_SEMANTIC_CONTRADICTION' in blockers else 'NEW_CANDIDATE')
    return {'state':state, 'identity_ready':identity_ready, 'full_ready':full_ready,
            'identity_blockers':blockers,'enrichment_blockers':content_blockers,
            'new_candidate_id':record.get('new_candidate_id'), 'new_group_id':group_id,
            'sterbrust_product_id':None,'diagnostic_only':True,'input_mutated':False}
