"""Pure post-matching GET proposal; no crawl, mutation or confirmation."""
from collections import Counter
from .intervesp_final_proposals import remaining_detail_buckets


def post_matching_detail_plan(rows, original_plan, checkpoint):
    remaining = remaining_detail_buckets(original_plan, checkpoint)
    fetched = {row['url'] for row in checkpoint['rows']}
    pending = {item['url']: item for item in original_plan if item['url'] not in fetched}
    selected, deferred = [], []
    ready_groups = {}
    for row in rows:
        if row.get('new_group_id') and row.get('readiness', {}).get('identity_ready'):
            ready_groups.setdefault(row['new_group_id'], []).append(row)
    full_groups = {gid for gid, members in ready_groups.items()
                   if any(r.get('readiness', {}).get('full_ready') for r in members)}
    selected_groups = set()
    ready_full_requests = []
    for row in sorted(rows, key=lambda r: (r['source'], str(r['external_id']))):
        if row['source'] != 'intervesp': continue
        url = row['source_url']
        if url in fetched: continue  # reuse saved evidence; never repeat GET
        item = pending.get(url)
        reason = None
        state = row['classification']
        if state == 'EXISTING_CONFIRMED' and item and item['reason'] in ('price', 'price_and_characteristics'):
            if row.get('price_state') in ('missing', 'ambiguous', 'parse_error', 'zero_invalid'):
                reason = 'confirmed_existing_missing_or_unreliable_price'
        elif row.get('readiness', {}).get('identity_ready') and not row['readiness'].get('full_ready'):
            group = row.get('new_group_id')
            if group and group not in full_groups and group not in selected_groups:
                # One source enrichment per proven canonical-new group first;
                # additional supplier content is independent, not extra NEW.
                reason = 'identity_ready_new_group_full_content'
                selected_groups.add(group)
                if not item: ready_full_requests.append({'url': url, 'new_group_id': group, 'reason': reason})
        elif state == 'REVIEW' and item and item['reason'] in ('characteristics', 'price_and_characteristics'):
            # Never use extra detail to chase a known suffix/accessory/semantic
            # contradiction. Only a same-full-model, own-machine candidate
            # with missing characteristic evidence is eligible for inspection.
            axis = row.get('axis_execution_evidence', {})
            conflicts = row.get('match_conflicts')
            if (row.get('full_model_confirmed') and row.get('proposed_sterbrust_id')
                    and row.get('model_role') == 'OWN_MODEL' and row.get('brand')
                    and row.get('product_kind') not in ('unknown', 'accessory', 'jaw', 'handle', 'controller')
                    and not conflicts and not axis.get('contradiction')
                    and row.get('match_method') in ('BRAND_MODEL_IDENTITY_REVIEW', 'BRAND_MODEL')):
                reason = 'review_same_full_model_missing_characteristic_evidence'
        if item:
            record = {'url': url, 'source': row['source'], 'external_id': str(row['external_id']),
                      'classification': state, 'original_reason': item['reason'], 'reason': reason}
            (selected if reason else deferred).append(record)
    if len({r['url'] for r in selected}) != len(selected): raise ValueError('Duplicate selective GET')
    return {'diagnostic_only': True, 'original_remaining_buckets': {k: len(v) for k, v in remaining.items()},
        'selected_urls': selected, 'selected_count': len(selected),
        'selected_reasons': dict(Counter(r['reason'] for r in selected)),
        'deferred_urls': deferred, 'deferred_count': len(deferred),
        'new_full_content_outside_original_plan': ready_full_requests,
        'minimum_proposed_gets': len(selected) + len(ready_full_requests),
        'minimum_pause_seconds': (len(selected) + len(ready_full_requests)) * 20,
        'unknown_availability_policy': 'unknown stays uncertainty; no speculative GET without useful source evidence',
        'fetched_url_policy': 'saved capture reuse only; no repeated detail GET'}
