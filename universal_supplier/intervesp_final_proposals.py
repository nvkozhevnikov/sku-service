"""Pure final-proposal gates for a pinned, identity-assessed manifest.

No HTTP, persistence, selection activation or Bitrix operation. The calling
publisher must supply the saved full manifest and current read-only snapshot.
"""
from collections import Counter

from .new_group_proposals import propose_new_groups, singleton_proposals, TYPED_GEOMETRIC_ANCHORS
from .new_readiness import propose_readiness
from .characteristic_evidence import evaluate_candidate, equipment_scope, features


def assert_enrichment_complete(report, expected_urls):
    expected = set(expected_urls)
    rows = report.get('rows', [])
    actual = [row['url'] for row in rows]
    if (report.get('stage') != 'DETAIL_ENRICHMENT_COMPLETE'
            or len(actual) != len(set(actual)) or set(actual) != expected
            or report.get('plan_count') != len(expected)
            or any(row.get('status') not in ('PERSISTED_ENRICHMENT', 'REVIEW', 'REVIEW_HTTP_TIMEOUT') for row in rows)):
        raise ValueError('Complete pinned enrichment checkpoint required; partial output is not final')
    return {row['url']: row for row in rows}


DETAIL_BUCKETS = {'identity': 'identity-critical', 'price': 'price-only',
                  'characteristics': 'characteristics-only',
                  'price_and_characteristics': 'price+characteristics'}


def remaining_detail_buckets(plan, report):
    """Disjoint pinned reasons; no assumption that all details are needed."""
    planned = {item['url']: item for item in plan}
    rows = report.get('rows', [])
    done = {row['url'] for row in rows}
    if len(planned) != len(plan) or len(done) != len(rows) or not done.issubset(planned):
        raise ValueError('Checkpoint/plan identity mismatch')
    buckets = {name: [] for name in DETAIL_BUCKETS.values()}
    for url, item in planned.items():
        if item['reason'] not in DETAIL_BUCKETS: raise ValueError('Unknown pinned detail reason')
        if url not in done: buckets[DETAIL_BUCKETS[item['reason']]].append(url)
    return buckets


def assert_matching_identity_complete(report, plan):
    """Final matching needs identity closure, not price/content completion.

    Reviewed contradictions/timeouts are closed uncertainty, never confirmed
    identity. Deferred price/content URLs are explicit in the final proof.
    """
    buckets = remaining_detail_buckets(plan, report)
    if (report.get('stage') not in ('IDENTITY_ENRICHMENT_COMPLETE', 'DETAIL_ENRICHMENT_COMPLETE')
            or report.get('plan_count') != len(plan) or buckets['identity-critical']
            or any(row.get('status') not in ('PERSISTED_ENRICHMENT', 'REVIEW', 'REVIEW_HTTP_TIMEOUT')
                   for row in report.get('rows', []))):
        raise ValueError('Pinned identity-critical checkpoint incomplete; final matching blocked')
    return {row['url']: row for row in report['rows']}


def apply_detail_quarantine(rows, checkpoint):
    """Later contradictory page evidence outranks an older successful parse."""
    output = []
    for row in rows:
        record = dict(row)
        detail = checkpoint.get(row['source_url']) if row['source'] == 'intervesp' else None
        if detail and detail['status'] in ('REVIEW', 'REVIEW_HTTP_TIMEOUT'):
            record.update(classification='REVIEW', sterbrust_product_id='', new_candidate_id='',
                          detail_review_reason=detail.get('reason'),
                          detail_review_evidence=detail.get('evidence_ref'),
                          readiness={'state': 'REVIEW', 'identity_ready': False, 'full_ready': False})
        output.append(record)
    return output


def preserve_unresolved_reviews(rows, prior):
    """No Beka source refresh occurred; prior card REVIEW is not auto-cleared."""
    output = []
    for original in rows:
        row = dict(original)
        old = prior.get((row['source'], str(row['external_id'])))
        if old and old['classification'] == 'REVIEW' and (
                row['source'] in ('beka_mak', 'beka_mak_tr')
                or old.get('match_status') == 'PRIOR_REVIEW_PENDING_OPERATOR'):
            row.update(classification='REVIEW', sterbrust_product_id='', new_candidate_id='',
                       prior_review_retained=True, prior_review_status=old.get('match_status'),
                       prior_review_evidence=old.get('match_conflicts') or old.get('match_warnings'),
                       match_conflicts={'prior_source_review_not_auto_cleared':True,
                                        'prior_conflicts':old.get('match_conflicts')})
        output.append(row)
    return output


def readiness_proposals(rows, learned, absence_index, *, absence_review_ref, allowed_sources=None):
    """Attach advisory gates without changing Existing or Conflict decisions.

    Complete active/inactive registry search is explicit and auditable. A
    model-only absence claim is never enough for readiness: independent typed
    identity, SAFE section and strict dedup are still mandatory.
    """
    rows = [scoped_group_anchors(r, learned) if r['classification'] in ('NEW_CANDIDATE', 'REVIEW')
            else dict(r) for r in rows]
    eligible = [r for r in rows if r['classification'] in ('NEW_CANDIDATE', 'REVIEW')
                and r.get('model')]
    grouping = propose_new_groups(eligible, **({'allowed_sources':allowed_sources} if allowed_sources is not None else {}))
    groups = grouping['groups'] + singleton_proposals(grouping)
    membership = {(r['source'], str(r['external_id'])): g for g in groups for r in g['source_records']}
    if sum(len(g['source_records']) for g in groups) != len(membership):
        raise ValueError('One source identity cannot belong to two NEW groups')
    reviews = {(r['source'], str(r['external_id'])): r for r in grouping['review']}
    result, absence_audit = [], []
    for original in rows:
        row = dict(original)
        if row['classification'] not in ('NEW_CANDIDATE', 'REVIEW'):
            result.append(row)
            continue
        identity = row['source'], str(row['external_id'])
        group = membership.get(identity)
        candidate = {**row, 'properties': row.get('observed_properties') or
                     [{'name': name, 'value': value}
                     for name, value in (row.get('properties') or {}).items()]}
        # First evaluate without proof references, then record the explicit
        # exhaustive saved-registry result. No historical NEW is used as truth.
        first = evaluate_candidate(candidate, learned, registry_index=absence_index)
        support = first['CANONICAL_ABSENCE_SUPPORT']
        audit = {'source': identity[0], 'external_id': identity[1], 'model': row.get('model'),
                 **support, 'review_method': 'complete_saved_active_inactive_exact_and_family_index',
                 'review_ref': absence_review_ref,
                 'scope': 'pinned_registry_snapshot_not_live_sterbrust'}
        absence_audit.append(audit)
        if support.get('support') and not first['CHARACTERISTIC_CONTRADICTION']['blocked']:
            candidate.update(absence_review_ref=absence_review_ref,
                             absence_registry_sha256=absence_index['sha256'])
        advisory = evaluate_candidate(candidate, learned, registry_index=absence_index,
                                      new_group=group, enable_ready_proposal=False)
        if identity in reviews and row['classification'] == 'NEW_CANDIDATE':
            row.update(canonical_match_classification='NEW_CANDIDATE', classification='REVIEW',
                       grouping_reasons=reviews[identity]['grouping_reasons'])
        row['readiness_advisory'] = advisory
        row['new_group_id'] = group['new_group_id'] if group else None
        row['readiness'] = propose_readiness(row, advisory, dedup_verified=bool(group),
            group_id=row['new_group_id'], enrichment=row.get('enrichment'))
        row['proposed_section_id'] = (advisory['SECTION_EVIDENCE']['mapping']['section_id']
                                      if advisory['SECTION_EVIDENCE']['verified'] else None)
        mapping = advisory['SECTION_EVIDENCE'].get('mapping') or {}
        row['proposed_section_path'] = next((section['path'] for example in mapping.get('examples', [])
            for section in example.get('sections', []) if section['id'] == row['proposed_section_id']), None)
        result.append(row)
    return result, {'groups': groups, 'review': grouping['review'],
                    'dedup_scope': 'current_pinned_supplier_registry_only'}, absence_audit


def scoped_group_anchors(row, learned):
    """Bridge real SAFE scoped units, without inventing a global synonym."""
    result = {**row, 'properties': dict(row.get('properties') or {})}
    scope = equipment_scope(row.get('name', ''), row.get('source_category', ''))
    context = row.get('source'), row.get('source_category'), scope
    allowed = {(m['supplier_property_name'], m['canonical_property_key']) for m in
               learned.get('synonym_mappings', []) if m['verdict'] == 'SAFE' and
               (m['source'], m['source_category'], m['category_scope']) == context}
    observed = features(row.get('observed_properties') or
                        [{'name': k, 'value': v} for k, v in (row.get('properties') or {}).items()], scope)
    anchors = {}
    for item in observed:
        key = 'typed_' + scope + '_' + item['key']
        if (key not in TYPED_GEOMETRIC_ANCHORS or not item['parsed'] or item['form'] != 'number'
                or item['canonical_unit'] != 'mm' or len(item['values']) != 1
                or (item['property_name'], item['key']) not in allowed): continue
        value = item['values'][0] + ' mm'
        if key in anchors and anchors[key] != value:
            # A local contradictory source must be REVIEW, not terminate all
            # independent proposals or lose the first raw property value.
            result['semantic_conflicts'] = {'prior': row.get('semantic_conflicts') or [],
                                            'typed_geometric_anchor': [key]}
            anchors = {}
            break
        anchors[key] = value
    result['properties'].update(anchors)
    result['typed_group_anchor_evidence'] = {'scope': scope, 'source_category': context[1],
        'anchors': anchors, 'basis': 'SAFE_confirmed_pairs_scoped_unit_conversion_only'}
    return result


def terminal_bucket(row):
    """Exclusive counts; supplemental blockers remain visible separately."""
    classification = row['classification']
    if classification != 'NEW_CANDIDATE':
        return classification
    ready = row.get('readiness', {})
    if ready.get('state') == 'REVIEW': return 'REVIEW'
    if ready.get('state') in ('READY_TO_CREATE_IDENTITY', 'READY_TO_CREATE_FULL'):
        return ready['state']
    blockers = ready.get('identity_blockers', [])
    for gate in ('NEW_NEEDS_ABSENCE_PROOF', 'NEW_NEEDS_SECTION', 'NEW_NEEDS_IDENTITY_EVIDENCE'):
        if gate in blockers: return gate
    return 'NEW_NEEDS_IDENTITY_EVIDENCE'


def final_counts(rows):
    return dict(Counter(terminal_bucket(row) for row in rows))
