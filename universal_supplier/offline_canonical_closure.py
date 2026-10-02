"""Strict saved-evidence expansion; no crawler, database or fuzzy acceptance."""
from copy import deepcopy
import json
from lxml import html
from .beka_canonical_offline import model_key
from .characteristic_evidence import property_key, equipment_scope, unit_from_label, features, IDENTITY_CRITICAL
from .offline_review_resolution import resolve_existing


def own_model_columns(record):
    """Only a uniquely labelled exact model column; neighbouring models ignored."""
    detail=(record.get('raw_data') or {}).get('detailText') or ''
    if not isinstance(detail,str) or not detail.strip():return []
    try:root=html.fromstring(detail)
    except (ValueError,html.etree.ParserError):return []
    key=model_key({'model':record.get('model_raw'),'brand':record.get('brand_raw'),'name':record['name']})
    scope=equipment_scope(record['name'],record.get('category_path',''))
    result=[]
    for ordinal,table in enumerate(root.xpath('//table')):
        trs=table.xpath('./tr|./thead/tr|./tbody/tr')
        if not trs:continue
        head=trs[0].xpath('./td|./th')
        if len(head)<3 or table.xpath('.//table|.//script|.//a'):continue
        titles=[' '.join(' '.join(c.itertext()).split()) for c in head]
        matches=[i for i,title in enumerate(titles[1:],1) if model_key({
            'model':title,'brand':record.get('brand_raw'),'name':title})==key]
        if len(matches)!=1 or not key:continue
        selected=matches[0]
        for tr in trs[1:]:
            cells=tr.xpath('./td|./th')
            if len(cells)!=len(head) or any(c.get('colspan') or c.get('rowspan') for c in cells):continue
            name,value=(' '.join(' '.join(c.itertext()).split()) for c in (cells[0],cells[selected]))
            if not name or not value or not property_key(name,scope):continue
            result.append({'name':name,'value':value,'unit':unit_from_label(name),
                'provenance':'saved_canonical_exact_model_column','canonical_id':str(record['sterbrust_product_id']),
                'table':ordinal,'column':selected,'model_header':titles[selected]})
    return result


def strict_saved_resolution(row,targets,mappings=()):
    """Exact retrieval is evaluated through existing full semantic/typed guards."""
    evaluations=[]
    for target in targets:
        view=deepcopy(row)
        conflicts=view.get('match_conflicts')
        if isinstance(conflicts,str):
            try:conflicts=json.loads(conflicts)
            except ValueError:pass
        view['match_conflicts']=conflicts or {}
        # This is an exact-evidence evaluator, not a replay of matcher scores.
        view.update(classification='REVIEW',match_status='HIGH_CONFIDENCE_MATCH')
        result=resolve_existing(view,target,mappings)
        evaluations.append({'canonical_id':target['sterbrust_product_id'],'canonical_url':target.get('product_url'),**result})
    passing=[r for r in evaluations if r['confirmed']]
    return {'confirmed':passing[0] if len(passing)==1 else None,'evaluations':evaluations,
            'blocking_reasons':sorted({r for e in evaluations for r in e['blocking_reasons']})
                if evaluations else ['EXACT_OWN_FULL_MODEL_CANDIDATE_MISSING']}


def evidence_route(row,audit,readiness):
    if audit.get('confirmed'):return 'AUTO_RESOLVED_EXISTING'
    if readiness.get('identity_ready'):return 'NEW_READINESS_CANDIDATE'
    if row.get('detail_review_reason') or row.get('listing_identity_reason'):
        return 'TARGETED_SUPPLIER_DETAIL_REQUIRED'
    reasons=set(audit.get('blocking_reasons') or [])
    if 'TYPED_CHARACTERISTIC_CONTRADICTION' in reasons:
        # Do not turn an unrelated retrieved candidate into a product conflict.
        return 'TARGETED_STERBRUST_PAGE_EVIDENCE_REQUIRED'
    if 'SOURCE_EXECUTION_FIELD_DISAGREES' in reasons:
        return 'FACTUAL_CONFLICT'
    if not row.get('brand') or not row.get('model') or not row.get('observed_properties'):
        return 'TARGETED_SUPPLIER_DETAIL_REQUIRED'
    return 'TARGETED_STERBRUST_PAGE_EVIDENCE_REQUIRED'


def source_support_inventory(row):
    """Presence of independently typed source anchors, never equivalence proof."""
    scope=equipment_scope(row.get('name',''),row.get('source_category',''))
    properties=row.get('observed_properties') or [{'name':k,'value':v} for k,v in (row.get('properties') or {}).items()]
    parsed=features(properties,scope)
    keys={p['key'] for p in parsed if p['parsed'] and p['importance']==IDENTITY_CRITICAL}
    geometry=keys-{'main_motor_power','main_spindle_power','voltage','automation_type','construction_type','blade_speed'}
    return {'scope':scope,'typed_identity_keys':sorted(keys),
            'independent_source_anchors_present':len(keys)>=2 and bool(geometry)}
