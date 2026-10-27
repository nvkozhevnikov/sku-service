"""Reconciled, per-record coverage evidence for read-only local previews."""
from collections import Counter
from .commercial_freshness import assess


def trace(row, reasons, *, warnings=(), selected=False, category=None):
    reasons=list(dict.fromkeys(reasons))
    return {'source':row['supplier_code'],'supplier_id':row.get('supplier_id'),
        'source_product_id':row['source_product_id'],'external_id':row['external_id'],
        'offer_id':row.get('offer_id'),'sterbrust_product_id':row.get('confirmed_sterbrust_id'),
        'match_status':row.get('match_status'),'identity_decision':row.get('decision'),
        'reasons':reasons,'primary_reason':reasons[0] if reasons else None,
        'warnings':list(dict.fromkeys(warnings)),'selected':selected,
        'category':category or ('eligible_preview' if not reasons else 'excluded')}


def summarize(rows, suppliers):
    result={}
    for source in suppliers:
        group=[r for r in rows if r['source']==source]
        primary=Counter(r['primary_reason'] for r in group if r['primary_reason'])
        eligible=sum(not r['reasons'] for r in group)
        assert eligible+sum(primary.values())==len(group)
        result[source]={'candidates':len(group),'eligible':eligible,
            'serialized_sources':sum(r['selected'] for r in group),
            'excluded':sum(primary.values()),'primary_reasons':dict(sorted(primary.items())),
            'all_reasons':dict(sorted(Counter(reason for r in group for reason in r['reasons']).items())),
            'non_blocking_warnings':dict(sorted(Counter(reason for r in group for reason in r['warnings']).items())),
            'categories':dict(sorted(Counter(r['category'] for r in group).items())),'reconciled':True}
    return result


def funnels(records, suppliers, settings, now, update_traces, new_traces):
    result={}
    for source in suppliers:
        rows=[r for r in records if r['supplier_code']==source]
        def count(predicate):return sum(bool(predicate(r)) for r in rows)
        fresh={id(r):assess(r,settings[source],now) for r in rows}
        confirmed=lambda r:r.get('identity_confirmed') is True and bool(r.get('confirmed_sterbrust_id'))
        allowed=lambda r:confirmed(r) and source!='beka_mak_tr' and not r.get('blocking_quarantine')
        price=lambda r:fresh[id(r)]['price']['state']=='fresh'
        stock=lambda r:fresh[id(r)]['availability']['state']=='fresh'
        u=[r for r in update_traces if r['source']==source]
        n=[r for r in new_traces if r['source']==source]
        # Explicitly independent branches: price and availability are never an AND gate.
        result[source]={'offers':len(rows),'confirmed_ids':count(confirmed),
            'allowed_source_identity':count(allowed),
            'price_available':count(lambda r:allowed(r) and fresh[id(r)]['price']['verified_at'] is not None),
            'price_fresh':count(lambda r:allowed(r) and price(r)),
            'availability_known':count(lambda r:allowed(r) and fresh[id(r)]['availability']['verified_at'] is not None),
            'availability_fresh':count(lambda r:allowed(r) and stock(r)),
            'any_fresh_commercial':count(lambda r:allowed(r) and (price(r) or stock(r))),
            'eligible_selection':sum(r.get('selection_eligible',False) for r in u),
            'preview_updates':sum(r['selected'] for r in u),
            'NEW':count(lambda r:r.get('match_status')=='NEW_CANDIDATE'),
            'NEW_current':count(lambda r:r.get('match_status')=='NEW_CANDIDATE' and r.get('matching_current_for_capture') is True),
            'NEW_no_canonical_duplicate':sum(r['match_status']=='NEW_CANDIDATE' and not
                {'EXISTING_CONFIRMED_OR_PHYSICAL_LINK','REGISTRY_IDENTITY_EXISTS'}.intersection(r['reasons']) for r in n),
            'NEW_semantic_allowed':sum(r['match_status']=='NEW_CANDIDATE' and not
                {'REVIEW_OR_CONFLICT','MULTI_VARIANT_SOURCE_REQUIRES_REVIEW','CROSS_SOURCE_PROPERTIES_REQUIRE_REVIEW',
                 'DIFFERENT_SOURCE_TITLES_REQUIRE_EXECUTION_REVIEW',
                 'OPERATOR_DECISION_REQUIRES_REVIEW','KNOWN_FALSE_MATCH'}.intersection(r['reasons']) for r in n),
            'NEW_identity_sufficient':sum(r['match_status']=='NEW_CANDIDATE' and r.get('identity_sufficient',False) for r in n),
            'NEW_fresh_content':count(lambda r:r.get('match_status')=='NEW_CANDIDATE' and fresh[id(r)]['content']['state']=='fresh'),
            'preview_new_sources':sum(r['selected'] for r in n)}
    return result


def regression(baseline, current, rows):
    """Zero after a positive baseline is diagnostic NOT_READY, never business PASS."""
    drops=[]
    if baseline:
        for kind in ('updates','new_products'):
            if baseline['counts'].get(kind,0)>0 and current['counts'][kind]==0:
                previous=baseline.get('included_sources',{}).get(kind,[])
                byid={r['source_product_id']:r for r in rows[kind]}
                explained=bool(previous) and all(i in byid and byid[i]['reasons'] for i in previous)
                drops.append({'kind':kind,'before':baseline['counts'][kind],'after':0,
                    'all_previous_records_explained':explained,'previous_source_ids':previous})
    empty=[kind for kind,count in current['counts'].items() if count==0]
    return {'status':('PARTIAL_NOT_READY' if any(current['counts'].values()) else 'NOT_READY')
                if drops or empty else 'PREVIEW_ONLY',
        'streams':{kind:'PREVIEW_ONLY' if count else 'NOT_READY' for kind,count in current['counts'].items()},
        'business_acceptance':'NOT_READY' if drops or empty else 'PREVIEW_ONLY_NOT_IMPORT_ACCEPTANCE',
        'release_blocked':bool(drops or empty),'regressions':drops}
