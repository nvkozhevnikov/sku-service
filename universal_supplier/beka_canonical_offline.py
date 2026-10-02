"""Scoped saved-registry retrieval and description evidence; no I/O or mutation.

Retrieval is deliberately not acceptance. Aliases and model families never
provide full-model equivalence. All acceptance uses the existing strict guards.
"""
from collections import defaultdict
from copy import deepcopy
import re
from lxml import html
from sterbrust_matching.normalization import normalize_brand, normalize_model
from .offline_review_resolution import clean_observed_model, source_title_model, resolve_existing
from .characteristic_evidence import property_key, equipment_scope, unit_from_label

NAMESPACES = frozenset(('beka_mak', 'beka_mak_tr'))


def model_key(row):
    return normalize_model(clean_observed_model(row)['model'], normalize_brand(row.get('brand')))


def family_key(model):
    # Candidate retrieval only: family includes executions sharing the digits.
    # No FUZZY and no numeric-neighbour retrieval (320 is never 325).
    match = re.match(r'^([a-z]+\d+)', normalize_model(model))
    return match.group(1) if match else ''


def saved_description_properties(record):
    """Two-cell own description tables; never related products or hidden SKU.

    Keep structured and description values side by side, so contradictions
    cannot be erased by preferring a convenient description value.
    """
    detail = record.get('raw_data', {}).get('detailText') or ''
    result = []
    if not isinstance(detail, str) or not detail.strip(): return result
    try: root = html.fromstring(detail)
    except (ValueError, html.etree.ParserError): return result
    scope = equipment_scope(record.get('name', ''), record.get('category_path', ''))
    for ordinal, tr in enumerate(root.xpath('//tr')):
        cells = tr.xpath('./td|./th')
        if len(cells) != 2 or tr.xpath('.//table|.//a|.//script'): continue
        name, value = (' '.join(' '.join(c.itertext()).split()) for c in cells)
        if not name or not value or not property_key(name, scope): continue
        # Numeric dimension separator only; not an execution/model rewrite.
        typed_value = re.sub(r'(?<=\d)\s*\*\s*(?=\d)', ' x ', value)
        result.append({'name': name, 'value': typed_value, 'unit': unit_from_label(name),
                       'raw_value': value, 'provenance': 'saved_canonical_detail_table',
                       'table_row': ordinal, 'canonical_id': str(record['sterbrust_product_id'])})
    return result


def canonical_view(record):
    props = [{'name': p['property_name'], 'value': p['value_flat'], 'unit': p.get('unit', ''),
              'provenance': 'saved_canonical_structured_property'}
             for p in record.get('properties', [])
             if p.get('property_name') and p.get('value_flat') not in (None, '', 'N')]
    props += saved_description_properties(record)
    return {'sterbrust_product_id': str(record['sterbrust_product_id']), 'name': record['name'],
            'model': record.get('model_raw', ''), 'brand': record.get('brand_raw', ''),
            'active': record.get('active'), 'source_category': record.get('category_path', ''),
            'observed_properties': props, 'properties': {p['name']: p['value'] for p in props},
            'captured_at': record.get('captured_at'), 'snapshot_hash': record.get('snapshot_hash'),
            'category_ids': record.get('category_ids', [])}


class SavedCandidateIndex:
    def __init__(self, targets):
        self.targets = targets
        self.exact, self.family = defaultdict(set), defaultdict(set)
        for pid, target in targets.items():
            if target.get('active') != 'Y': continue
            brand = normalize_brand(target.get('brand'))
            # Index full model and independently visible title, not aliases.
            for model in (clean_observed_model(target)['model'], source_title_model(target)):
                key = normalize_model(model, brand)
                if not key: continue
                self.exact[(brand, key)].add(pid)
                self.family[(brand, family_key(model))].add(pid)
        for target in targets.values():
            target['equivalent_identity_ids'] = sorted(self.exact[(normalize_brand(target.get('brand')), model_key(target))])

    def retrieve(self, row):
        brand, key = normalize_brand(row.get('brand')), model_key(row)
        exact = sorted(self.exact.get((brand, key), ()))
        family = sorted(self.family.get((brand, family_key(clean_observed_model(row)['model'])), set()) - set(exact))
        return {'exact_full_model_ids': exact, 'family_retrieval_only_ids': family,
                'alias_auto_accept': False, 'fuzzy_auto_accept': False}


def evaluate_row(row, index):
    retrieval = index.retrieve(row)
    candidates = []
    for pid in retrieval['exact_full_model_ids'] + retrieval['family_retrieval_only_ids']:
        target = index.targets[pid]
        view = deepcopy(row)
        # A new exact retrieval can be evaluated independently of an old fuzzy
        # candidate. Existing source quarantines/operator holds remain intact.
        view.update(classification='REVIEW', match_status='HIGH_CONFIDENCE_MATCH')
        result = resolve_existing(view, target)
        candidates.append({'sterbrust_product_id': pid, 'name': target['name'], 'model': target['model'],
                           'retrieval_only': pid not in retrieval['exact_full_model_ids'], **result})
    passing = [c for c in candidates if c['confirmed'] and not c['retrieval_only']]
    # A rejected family candidate must not inflate the blockers for an exact
    # full-model candidate. Preserve family diagnostics separately.
    decisive = [c for c in candidates if not c['retrieval_only']]
    reasons = sorted({x for c in decisive for x in c['blocking_reasons']})
    if not retrieval['exact_full_model_ids']: reasons.append('EXACT_FULL_MODEL_NOT_IN_PINNED_REGISTRY')
    if row.get('prior_review_retained'): reasons.append('PRIOR_REVIEW_REQUIRES_OPERATOR')
    if row.get('detail_review_reason') or row.get('listing_identity_reason'): reasons.append('SOURCE_IDENTITY_QUARANTINED')
    if len(passing) != 1: confirmed = None
    else: confirmed = passing[0]
    return {'source': row['source'], 'external_id': str(row['external_id']), 'model': row.get('model'),
            'source_url': row.get('source_url'), 'retrieval': retrieval, 'candidates': candidates,
            'confirmed': confirmed, 'blocking_reasons': [] if confirmed else sorted(set(reasons))}


def apply_proposals(rows, index):
    output, audits, changes = deepcopy(rows), [], []
    for row in output:
        if row.get('source') not in NAMESPACES or row.get('classification') == 'EXISTING_CONFIRMED': continue
        audit = evaluate_row(row, index)
        before = row['classification']
        chosen = audit['confirmed']
        if chosen and before == 'REVIEW':
            row.update(classification='EXISTING_CONFIRMED', sterbrust_product_id=chosen['sterbrust_product_id'],
                       sterbrust_name=chosen['name'], full_model_confirmed=True, proposed_sterbrust_id='',
                       canonical_offline_resolution=chosen,
                       readiness={'state': 'EXISTING_CONFIRMED', 'identity_ready': False, 'full_ready': False})
        # A model-only mismatch against an unrelated numeric family is a
        # rejected retrieval, not evidence of a contradiction within the source
        # identity. Never apply this to source holds, semantic/typed/axis/condition
        # conflicts or an exact full-model target. Never turn it into Existing/NEW.
        old_id = str(row.get('proposed_sterbrust_id'))
        old_target = index.targets.get(old_id)
        conflicts = row.get('match_conflicts', {})
        mismatch = conflicts.get('model', {}) if isinstance(conflicts, dict) else {}
        only_unrelated_model = (
            set(conflicts)=={'model'} and isinstance(mismatch, dict)
            and mismatch.get('source')==model_key(row)
            and old_target and mismatch.get('candidate')==model_key(old_target)
            and family_key(row.get('model'))!=family_key(old_target.get('model'))
            and not row.get('prior_review_retained') and not row.get('detail_review_reason')
            and not row.get('listing_identity_reason') and not row.get('semantic_conflicts')
            and not row.get('axis_execution_evidence',{}).get('blocked')
            and not row.get('condition_execution_evidence',{}).get('blocked'))
        if (before == 'CONFLICT' and only_unrelated_model
                and not audit['retrieval']['exact_full_model_ids']):
            row.update(classification='REVIEW', proposed_sterbrust_id='',
                       canonical_offline_resolution={'reason': 'UNRELATED_RETRIEVAL_NOT_SOURCE_CONFLICT',
                         'rejected_candidate_id': old_id, 'audit': audit},
                       readiness={'state': 'REVIEW', 'identity_ready': False, 'full_ready': False})
        if row['classification'] != before:
            changes.append({'source': row['source'], 'external_id': row['external_id'],
                            'before': before, 'after': row['classification']})
        audit['after'] = row['classification']
        audits.append(audit)
    return output, audits, changes


def blocker_route(audit):
    if audit['after'] == 'EXISTING_CONFIRMED': return 'CLOSED_OFFLINE'
    reasons = set(audit['blocking_reasons'])
    if reasons & {'PRIOR_REVIEW_REQUIRES_OPERATOR', 'SOURCE_IDENTITY_QUARANTINED', 'SOURCE_EXECUTION_FIELD_DISAGREES'}:
        return 'HUMAN_OR_AUTHORITATIVE_IDENTITY_EVIDENCE'
    if 'EXACT_FULL_MODEL_NOT_IN_PINNED_REGISTRY' in reasons: return 'FRESH_STERBRUST_REGISTRY'
    return 'ADDITIONAL_CANONICAL_SIDE_EVIDENCE'
