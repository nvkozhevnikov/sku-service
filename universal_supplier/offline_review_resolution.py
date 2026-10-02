"""Evidence-only review resolution. No database, HTTP or production operations."""
from collections import Counter, defaultdict
from copy import deepcopy
import re

from sterbrust_matching.normalization import normalize_model, normalize_brand, extract_model
from sterbrust_matching.matching import Product, classify_identity_quality
from sterbrust_matching.product_identity import classify_model_role, classify_product_kind, OWN_MODEL
from .characteristic_evidence import features, equipment_scope, _same, IDENTITY_CRITICAL, normalize_characteristic, unit_from_label
from .execution_evidence import axis_execution_guard, condition_execution_guard


def clean_observed_model(row):
    """Only grammatical CNC prefix and explicitly observed leading brand.

    Never remove execution/suffix tokens or infer the model from a target.
    Raw identity and candidate IDs remain unchanged in the source record.
    """
    raw = str(row.get('model') or '').strip()
    text = re.sub(r'^(?:с\s+)?ЧПУ\s+', '', raw, flags=re.I)
    brand = str(row.get('brand') or '').strip()
    # Source may explicitly spell brand as BLIN MACHINERY but print BLIN in
    # its own title. This is a local textual prefix rule, not a brand alias.
    prefixes = [brand]
    if brand.upper().endswith(' MACHINERY'):
        short = brand[:-len(' MACHINERY')].strip()
        if re.search(r'(?<!\w)' + re.escape(short) + r'(?!\w)', str(row.get('name')), re.I):
            prefixes.append(short)
    for prefix in sorted(filter(None, prefixes), key=len, reverse=True):
        text = re.sub(r'^' + re.escape(prefix) + r'\s+', '', text, flags=re.I)
    return {'raw_model': raw, 'model': text, 'changed': text != raw,
            'rule': 'explicit_CNC_grammar_and_observed_brand_prefix_only'}


def source_title_model(row):
    title = str(row.get('name') or '')
    brand = str(row.get('brand') or '')
    prefixes = [brand]
    if brand.upper().endswith(' MACHINERY'): prefixes.append(brand[:-10].strip())
    for prefix in sorted(filter(None, prefixes), key=len, reverse=True):
        title = re.sub(r'(?<!\w)' + re.escape(prefix) + r'(?!\w)', ' ', title, flags=re.I)
    return extract_model(title)


def cnc_features(properties, scope):
    """Explicit lathe semantics, never swing/bore/turning-diameter conflation."""
    if scope != 'lathe': return []
    result=[]
    for p in properties:
        name=p['name'].lower()
        key=None
        if re.search(r'^макс\.?\s*длина обработки',name): key,converter='max_processing_length','blade_length'
        elif re.search(r'^мощность шпинделя\s*(?:,|\()',name): key,converter='main_spindle_power','main_motor_power'
        elif re.search(r'^ход по оси x\s*(?:,|\()',name): key,converter='axis_x_travel','blade_length'
        else: continue
        parsed=normalize_characteristic(p['value'],converter,p.get('unit') or unit_from_label(name))
        result.append({'property_name':p['name'],'key':key,'importance':IDENTITY_CRITICAL,
                       'raw_value':p['value'],'raw_unit':p.get('unit') or unit_from_label(name),**parsed})
    return result


def learn_cnc_mappings(confirmed_pairs):
    """Only confirmed pairs; minimum 3 examples and unanimous typed values."""
    observations=defaultdict(list)
    for p in confirmed_pairs:
        if p.get('classification')!='EXISTING_CONFIRMED' or not p.get('full_model_confirmed'): continue
        scope=p.get('equipment_type')
        for a in cnc_features(p.get('source_properties',[]),scope):
            for b in cnc_features(p.get('sterbrust_properties',[]),scope):
                if a['key']!=b['key'] or not a['parsed'] or not b['parsed']: continue
                observations[(p['source'],scope,a['property_name'],b['property_name'],a['key'])].append(
                    {'source_external_id':p['external_id'],'sterbrust_product_id':p['sterbrust_product_id'],
                     'agreement':_same(a,b),'source':a,'canonical':b})
    return [{'source':k[0],'scope':k[1],'source_property':k[2],'canonical_property':k[3],'key':k[4],
             'verdict':'SAFE' if len({x['source_external_id'] for x in v})>=3 and all(x['agreement'] for x in v) else 'REVIEW',
             'examples':v} for k,v in sorted(observations.items())]


def compare_typed(row, target, mappings=()):
    scope = equipment_scope(row.get('name', ''), row.get('source_category', ''))
    source = features(row.get('observed_properties') or
                      [{'name': k, 'value': v} for k, v in row.get('properties', {}).items()], scope)
    reference = features(target.get('observed_properties', []), scope)
    for mapping in mappings:
        if mapping['verdict']!='SAFE' or mapping['source']!=row.get('source') or mapping['scope']!=scope: continue
        source.extend(f for f in cnc_features(row.get('observed_properties',[]),scope)
                      if f['property_name']==mapping['source_property'] and f['key']==mapping['key'])
        reference.extend(f for f in cnc_features(target.get('observed_properties',[]),scope)
                         if f['property_name']==mapping['canonical_property'] and f['key']==mapping['key'])
    left, right = defaultdict(list), defaultdict(list)
    for f in source: left[f['key']].append(f)
    for f in reference: right[f['key']].append(f)
    contradictions, agreements = [], []
    for key, values in left.items():
        if len({(v['form'], tuple(v['values'])) for v in values if v['parsed']}) > 1:
            contradictions.append({'key': key, 'reason': 'source_values_disagree'})
    for key, values in right.items():
        if len({(v['form'], tuple(v['values'])) for v in values if v['parsed']}) > 1:
            contradictions.append({'key': key, 'reason': 'canonical_values_disagree'})
    for key in left.keys() & right.keys():
        for a in left[key]:
            for b in right[key]:
                if not a['parsed'] or not b['parsed']: continue
                pair = {'key': key, 'source': a, 'canonical': b}
                if _same(a, b) and a['importance'] == IDENTITY_CRITICAL: agreements.append(pair)
                elif not _same(a, b) and a['importance'] == IDENTITY_CRITICAL: contradictions.append(pair)
    keys = {p['key'] for p in agreements}
    geometry = keys - {'main_motor_power', 'main_spindle_power', 'voltage', 'automation_type', 'construction_type', 'blade_speed'}
    return {'scope': scope, 'agreements': agreements, 'contradictions': contradictions,
            'independent_identity_support': len(keys) >= 2 and bool(geometry)}


def resolve_existing(row, target, mappings=()):
    """Require exact own full model, both semantic directions and typed anchors."""
    reasons, proof = [], {'model_normalization': clean_observed_model(row)}
    model = proof['model_normalization']['model']
    if row.get('classification') != 'REVIEW' or row.get('match_status') != 'HIGH_CONFIDENCE_MATCH':
        reasons.append('NOT_HIGH_CONFIDENCE_REVIEW')
    if row.get('prior_review_retained'): reasons.append('PRIOR_REVIEW_REQUIRES_OPERATOR')
    if row.get('detail_review_reason') or row.get('listing_identity_reason'):
        reasons.append('SOURCE_IDENTITY_QUARANTINED')
    if row.get('match_conflicts') or row.get('semantic_conflicts'): reasons.append('UPSTREAM_CONTRADICTION')
    if not target or not str(target.get('sterbrust_product_id', '')).isdigit():
        return {'confirmed': False, 'blocking_reasons': sorted(set(reasons + ['CANONICAL_TARGET_MISSING'])), 'evidence': proof}
    if target.get('active') != 'Y': reasons.append('CANONICAL_TARGET_NOT_ACTIVE')
    brand = normalize_brand(row.get('brand'))
    other_brand = normalize_brand(target.get('brand'))
    if not brand or not other_brand or brand != other_brand: reasons.append('BRAND_NOT_EXACTLY_COMPATIBLE')
    if 'equivalent_identity_ids' in target and target['equivalent_identity_ids'] != [target['sterbrust_product_id']]:
        reasons.append('CANONICAL_IDENTITY_NOT_UNIQUE')
    canonical_raw = target.get('model') or extract_model(target.get('name'))
    canonical_normalization = clean_observed_model({**target,'model':canonical_raw})
    proof['canonical_model_normalization'] = canonical_normalization
    canonical_model = canonical_normalization['model']
    if not normalize_model(model, brand) or normalize_model(model, brand) != normalize_model(canonical_model, other_brand):
        reasons.append('FULL_MODEL_EXECUTION_MISMATCH')
    if normalize_model(source_title_model(row), brand) != normalize_model(model, brand):
        reasons.append('SOURCE_TITLE_FULL_MODEL_NOT_PROVEN')
    if normalize_model(source_title_model(target), other_brand) != normalize_model(canonical_model, other_brand):
        reasons.append('CANONICAL_TITLE_FULL_MODEL_NOT_PROVEN')
    if row.get('execution') and clean_observed_model({**row, 'model': row['execution']})['model'] != model:
        reasons.append('SOURCE_EXECUTION_FIELD_DISAGREES')
    source_kind = classify_product_kind(row.get('name',''),row.get('source_category','')).product_kind
    target_kind = classify_product_kind(target.get('name',''),target.get('source_category','')).product_kind
    if source_kind in ('unknown','accessory','jaw','handle','controller') or source_kind != target_kind:
        reasons.append('PRODUCT_CONTEXT_NOT_EXACT')
    if (row.get('model_role') != OWN_MODEL or
            classify_model_role(row.get('name',''),row.get('source_category',''),model).role != OWN_MODEL or
            classify_model_role(target.get('name',''),target.get('source_category',''),canonical_model).role != OWN_MODEL):
        reasons.append('OWN_MODEL_NOT_PROVEN')
    a = Product('source',row.get('name',''),brand,model,category=row.get('source_category',''))
    b = Product('target',target.get('name',''),other_brand,canonical_model,category=target.get('source_category',''))
    proof['semantic_checks'] = []
    for x,y in ((a,b),(b,a)):
        quality, evidence, conflicts, warnings = classify_identity_quality(x,y)
        proof['semantic_checks'].append({'quality':quality,'evidence':evidence,'conflicts':conflicts,'warnings':warnings})
        if quality or conflicts or warnings: reasons.append('SEMANTIC_GUARD_BLOCKED')
    for label, guard in (('axis',axis_execution_guard),('condition',condition_execution_guard)):
        evidence = guard(a.name,row.get('properties',{}),b.name,target.get('properties',{}))
        proof[label] = evidence
        if evidence['blocked']: reasons.append(label.upper()+'_EXECUTION_GUARD_BLOCKED')
    proof['typed_characteristics'] = compare_typed(row,target,mappings)
    if proof['typed_characteristics']['contradictions']: reasons.append('TYPED_CHARACTERISTIC_CONTRADICTION')
    if not proof['typed_characteristics']['independent_identity_support']:
        reasons.append('INDEPENDENT_TYPED_IDENTITY_SUPPORT_MISSING')
    if not row.get('evidence_ref'): reasons.append('SOURCE_PROVENANCE_MISSING')
    proof['canonical_product_id'] = target['sterbrust_product_id']
    return {'confirmed': not reasons, 'blocking_reasons': sorted(set(reasons)), 'evidence': proof}


def reason_clusters(audits):
    """One count per row/gate, not repeated property/pair occurrences."""
    counter = Counter(reason for audit in audits for reason in set(audit['blocking_reasons']))
    return [{'reason':reason,'blocked_rows':count} for reason,count in sorted(counter.items(),key=lambda p:(-p[1],p[0]))]
