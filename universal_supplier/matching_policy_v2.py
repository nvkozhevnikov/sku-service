"""Operator-confirmed identity policy (2026-10-08), independent of ingestion.

Exact retrieval is not acceptance. Characteristics can contradict or
disambiguate, but their absence never imposes a positive anchor quota.
The API is supplier-neutral; enabling it does not rerun predecessor decisions.
"""
from collections import defaultdict
from decimal import Decimal
import re

from sterbrust_matching.normalization import normalize_brand, normalize_model, normalize_text, extract_model
from sterbrust_matching.product_identity import classify_product_kind, classify_model_role, OWN_MODEL
from sterbrust_matching.product_identity import execution_class_marks
from .characteristic_evidence import features, equipment_scope, _same, IDENTITY_CRITICAL, normalize_characteristic, unit_from_label
from .execution_evidence import axis_execution_guard, condition_execution_guard
from .offline_review_resolution import clean_observed_model

RULE_VERSION = 'matching-policy-v2.1-2026-10-08'

# Identity aliases are deliberately finite and scoped. Search synonyms are NOT
# consulted by acceptance. No global EcoWood/HCM or arbitrary brand equivalence.
BRAND_IDENTITY_ALIASES = {
    ('ecowood', 'hcm', 'srrp630'), ('ecowood', 'hcm', 'srrp1300'),
    # Operator-confirmed exact Osama card: canonical brand field says Griggio,
    # while its visible name explicitly identifies Osama. Not a global rebrand.
    ('osama', 'griggio', 's2r1300'),
}
MODEL_IDENTITY_ALIASES = {
    ('ermaksan', 'powerbend210040'): 'powerbendfalcon2100x40',
    ('ermaksan', 'powerbendfalcon2100x40'): 'powerbendfalcon2100x40',
    ('filato', normalize_model('Boring 23 шпинделя')): 'boring23',
}
NON_MACHINE = {'steady_rest','foot_switch','parts_catcher','faceplate','chuck_jaw','jaw',
               'handle','controller','collet','chuck','disk','table','stand','fixture',
               'accessory','tool','consumable'}
EXTRA_KINDS = (
    ('plate_roll', r'вальц|\bplate rolls?\b'),
    ('press_brake', r'листогиб|\bpress brake\b'),
    ('shear', r'гильотин|ножницы|\bshear'),
    ('compressor', r'компрессор|\bcompressor'),
    ('robot', r'\bробот|\brobot'),
    ('saw', r'пил[ьи]|пильн|\bsaw'),
    ('lathe', r'токарн|\blathe|\bturning'),
    ('milling_machine', r'фрезерн|\bmilling'),
)


def plus_evidence(text):
    """Classify each plus from original model/title tokens, not a global strip.

    Compact execution codes/uppercase options/numeric model tokens carry plus;
    ordinary prose such as 'machine + options' does not. Ambiguous model plus
    is preserved, not silently downgraded to punctuation.
    """
    result=[]
    for match in re.finditer(r'\+',str(text or '')):
        left=re.search(r'([\w./-]+)\s*$',str(text)[:match.start()])
        right=re.match(r'\s*([\w./-]+)',str(text)[match.end():])
        a=left.group(1) if left else '';b=right.group(1) if right else ''
        code=lambda t: bool(t and (re.search(r'\d',t) or (re.search(r'[A-ZА-Я]',t) and t.upper()==t)))
        descriptive=bool(a and b and not code(a) and not code(b) and len(a)>1 and len(b)>1)
        result.append({'position':match.start(),'left':a,'right':b,
                       'kind':'PUNCTUATION_PLUS' if descriptive else 'EXECUTION_PLUS'})
    return result


def normalize_identity_model(model,brand=''):
    """Keep semantic + as an actual delimiter (X+Y is never XY or X).

    Used by V2 only: legacy source/registry hash normalization is not changed.
    """
    text=str(model or '')
    marked=''.join('+' if e['kind']=='EXECUTION_PLUS' else '' for e in plus_evidence(text))
    if not marked: return normalize_model(text,brand)
    parts=re.split(r'\+',text)
    # Brand prefix belongs only to the first component, never an option code.
    return '+'.join(normalize_model(p,brand if i==0 else '') for i,p in enumerate(parts))


def identity_model(row):
    raw = clean_observed_model(row)['model']
    key = normalize_identity_model(raw, row.get('brand', ''))
    return MODEL_IDENTITY_ALIASES.get((normalize_brand(row.get('brand')), key), key)


def usable_model(model, brand=''):
    key = normalize_model(model, brand)
    return bool(key and re.search(r'[a-zа-я]', key) and re.search(r'\d', key)
                and not re.fullmatch(r'\d+(?:mm|мм|cm|см|kw|квт|w|v|rpm)', key)
                and key != normalize_model(brand))


def title_represents(title, model, brand=''):
    """Full token, not a base-model substring; suffixes remain significant.

    A family title is acceptable only when the canonical own model field
    identifies this exact execution, separately checked by assess_pair().
    """
    raw = normalize_text(model).replace('×', 'x').translate(str.maketrans({'х':'x'}))
    chars = re.findall(r'[a-zа-я0-9+]', raw)
    if not chars: return False
    pattern = r'(?<![a-zа-я0-9+])' + r'[\s\-./(),×x]*'.join(map(re.escape, chars)) + r'(?![a-zа-я0-9+])'
    text = normalize_text(title).replace('×', 'x').translate(str.maketrans({'х':'x'}))
    for match in re.finditer(pattern, text, re.I):
        tail = text[match.end():]
        # A standalone qualifier/short execution immediately following the
        # base model is not generic marketing wording.
        if re.match(r'^\s+(?:new|pro|plus|premium|vario|cnc|[a-z]{1,3})\b', tail):
            continue
        return True
    return False


def sold_kind(row):
    kind = classify_product_kind(row.get('name',''), row.get('source_category','')).product_kind
    if kind in NON_MACHINE: return kind
    text = normalize_text(row.get('name',''))
    for name, pattern in EXTRA_KINDS:
        if re.search(pattern, text): return name
    return kind


def _role(prop, key):
    text = normalize_text(str(prop.get('role','')) + ' ' + prop['name'])
    # Unknown power duty cycles are not interchangeable scalar evidence.
    if re.search(r'\bs6\b|continuous|cont\.|пиков|нетто|брутто|gross|net', text):
        return normalize_text(prop.get('role','')) or normalize_text(prop['name'])
    if re.search(r'противошпиндел|counter.?spindle|sub.?spindle', text): return 'counter_spindle'
    if re.search(r'приводн.*инструмент|револьвер|turret', text): return 'turret'
    if re.search(r'задн.*бабк|tailstock', text): return 'tailstock'
    if key == 'spindle_bore': return 'main_spindle' if not re.search(r'противо|counter|sub',text) else 'counter_spindle'
    if key == 'main_motor_power':
        if re.search(r'шпиндел|spindle',text): return 'main_spindle'
        if re.search(r'главн|основн|двигател|motor',text): return 'main_motor'
        return 'unspecified_power'
    return key


def semantic_numeric(prop,scope):
    """Explicit same-field meanings; no numeric-value-based synonym learning."""
    label=normalize_text(prop['name']);context=normalize_text(prop.get('role',''))
    key=converter=None
    role=''
    if re.search(r'мощност|\bpower\b|^главный привод|^main drive',label):
        if re.search(r'установлен|установоч|потребляем|суммарн|total|installed',label): role='installed_total'
        elif re.search(r'сож|coolant',label+' '+context): role='coolant_pump'
        elif re.search(r'гидронасос|hydraulic.*pump',label+' '+context): role='hydraulic_pump'
        elif re.search(r'гидравлическ.*привод|hydraulic.*drive',label+' '+context): role='hydraulic_drive'
        elif re.search(r'подрезн|scoring',label+' '+context): role='scoring_saw'
        elif re.search(r'полиров|polish',label+' '+context): role='polishing_motor'
        elif re.search(r'подач|feed',label+' '+context): role='feed_drive'
        elif re.search(r'поворот|rotation',label+' '+context): role='rotation_drive'
        elif re.search(r'приводн.*инструмент|револьвер|turret',label+' '+context): role='turret'
        elif re.search(r'противошпиндел|counter.?spindle|sub.?spindle',label+' '+context): role='counter_spindle'
        elif re.search(r'шпиндел|spindle',label+' '+context): role='main_spindle'
        elif re.search(r'главн|основн|main motor|main drive',label): role='main_motor'
        elif re.search(r'двигател|motor',label): role='main_motor' if scope=='bandsaw' else 'motor_unspecified_function'
        elif scope=='bandsaw': role='main_motor'
        else: role='unproven:'+label+':'+context
        duty=[]
        for code,pattern in [('S6',r'\bs6\b'),('CONT',r'continuous|cont\.|\bcont\b|длительн|номинальн'),
                             ('MAX',r'максимальн|\bmax\b|пиков')]:
            if re.search(pattern,label+' '+context): duty.append(code)
        role+=':'+','.join(duty or ['UNSPECIFIED_DUTY'])
        key,converter='power','main_motor_power'
    else:
        for pattern,semantic in (
            (r'^(?:макс\.?|максимальная)\s*длина (?:обработки|точения)|^max(?:imum)? (?:turning|processing) length','max_processing_length'),
            (r'^(?:макс\.?|максимальный)\s*диаметр точения|^max(?:imum)? turning diameter','max_turning_diameter'),
            (r'^(?:макс\.?|максимальный)\s*диаметр обработки','max_processing_diameter'),
            (r'^(?:макс\.?|максимальный)\s*диаметр вращения|^max(?:imum)? swing diameter','max_swing_diameter'),
            (r'^(?:макс\.?\s*)?рабочая длина|^working length','working_length'),
        ):
            if re.search(pattern,label):
                key,converter=semantic,'blade_length';role=semantic
                if re.search(r'над станин|over bed',label): role+=':above_bed'
                elif re.search(r'над суппорт|над салазк|over carriage|over slide',label): role+=':above_carriage'
                else: role+=':unspecified_position'
                break
    if not key: return None
    parsed=normalize_characteristic(prop.get('value'),converter,prop.get('unit') or unit_from_label(label))
    return {'property_name':prop['name'],'key':key,'role':role,'importance':IDENTITY_CRITICAL,
            'provenance':prop.get('provenance'),**parsed}


def typed_fields(row,scope):
    grouped=defaultdict(list)
    for prop in row.get('observed_properties',[]):
        custom=semantic_numeric(prop,scope)
        items=[custom] if custom else features([prop],scope)
        for item in items:
            if item.get('derivation') or not item['parsed'] or item['importance']!=IDENTITY_CRITICAL: continue
            grouped[(item['key'],item.get('role') or _role(prop,item['key']))].append(item)
    return grouped


def material_difference(a,b):
    if a['canonical_unit']!=b['canonical_unit'] or a['form']!=b['form']: return None
    if a['form']=='text': return a['values']!=b['values']
    if len(a['values'])!=len(b['values']): return None
    # Round-off is not a material execution change. Roles and shape must be
    # aligned first; ranges/options are not flattened to a convenient scalar.
    return any(abs(Decimal(x)-Decimal(y))>max(Decimal('0.01'),max(abs(Decimal(x)),abs(Decimal(y)))*Decimal('0.01'))
               for x,y in zip(a['values'],b['values']))


def characteristic_evidence(left, right):
    scope = equipment_scope(left.get('name',''),left.get('source_category',''))
    sides = [typed_fields(row,scope) for row in (left,right)]
    agreements, contradictions,uncertain = [], [],[]
    for key in sides[0].keys() & sides[1].keys():
        a,b = sides[0][key],sides[1][key]
        if key[0]=='power' and (key[1].startswith('unproven:') or key[1].startswith('motor_unspecified_function')):
            # Repeated generic motor labels on a multi-motor machine do not
            # prove equal function. Retain all values, never choose a matching
            # scalar nor cross-compare feed/polish/scoring/rotation roles.
            if any(len({tuple(v['values']) for v in side})>1 for side in (a,b)):
                uncertain.append({'key':key[0],'role':key[1],'source_values':a,'canonical_values':b,
                                  'reason':'multiple_generic_motor_functions_not_aligned'})
                continue
        # No convenient-value selection: repeated same-role values must agree.
        for x in a:
            for y in b:
                pair = {'key':key[0],'role':key[1],'source':x,'canonical':y}
                difference=material_difference(x,y)
                if _same(x,y) or difference is False: agreements.append(pair)
                elif difference is None: uncertain.append({**pair,'reason':'role_shape_or_unit_not_comparable'})
                elif x['key']=='blade_dimensions': uncertain.append({**pair,'reason':'blade_vector_component_order_not_proven'})
                else: contradictions.append(pair)
    return {'scope':scope,'agreements':agreements,'contradictions':contradictions,
            'uncertain':uncertain,'positive_characteristic_quota':0}


def canonical_view_v2(record):
    """Retain saved own power/critical rows that the older quota parser omitted."""
    from .beka_canonical_offline import canonical_view
    from lxml import html
    result=canonical_view(record)
    scope=equipment_scope(result['name'],result['source_category'])
    detail=record.get('raw_data',{}).get('detailText') or ''
    if not isinstance(detail,str) or not detail.strip(): return result
    try: root=html.fromstring(detail)
    except (ValueError,html.etree.ParserError): return result
    seen={(p['name'],p['value'],p.get('role','')) for p in result['observed_properties']}
    for table in root.xpath('//table'):
        role=''
        for tr in table.xpath('./tr|./tbody/tr|./thead/tr'):
            cells=tr.xpath('./td|./th')
            if tr.xpath('.//table|.//a|.//script'): continue
            strings=[' '.join(' '.join(c.itertext()).split()) for c in cells]
            if len(strings)==1:
                if re.search(r'шпиндел|двигател|привод|пил|подач|полиров|поворот|s6|cont|max',normalize_text(strings[0])): role=strings[0]
                continue
            if len(strings)!=2 or not all(strings): continue
            prop={'name':strings[0],'value':strings[1],'unit':unit_from_label(strings[0]),'role':role,
                  'provenance':'saved_canonical_own_table_v2.1','canonical_id':result['sterbrust_product_id']}
            if semantic_numeric(prop,scope) and (prop['name'],prop['value'],role) not in seen:
                result['observed_properties'].append(prop);seen.add((prop['name'],prop['value'],role))
    return result


def assess_pair(source, target):
    reasons = []
    sb,tb = normalize_brand(source.get('brand')),normalize_brand(target.get('brand'))
    sm,tm = identity_model(source),identity_model(target)
    alias = (sb,tb,sm) in BRAND_IDENTITY_ALIASES or (tb,sb,tm) in BRAND_IDENTITY_ALIASES
    if not sb or not tb or (sb!=tb and not alias): reasons.append('BRAND_INCOMPATIBLE')
    if target.get('active')!='Y' or not str(target.get('sterbrust_product_id','')).isdigit(): reasons.append('CANONICAL_NOT_ACTIVE_REAL_ID')
    if not usable_model(source.get('model',''),sb): reasons.append('SOURCE_FULL_MODEL_NOT_PROVEN')
    if not sm or sm != tm: reasons.append('FULL_EXECUTION_MISMATCH')
    sk,tk = sold_kind(source),sold_kind(target)
    if sk in NON_MACHINE or tk in NON_MACHINE or sk=='unknown' or tk=='unknown' or sk!=tk:
        reasons.append('SOLD_PRODUCT_KIND_NOT_COMPATIBLE')
    for row,label in ((source,'SOURCE'),(target,'CANONICAL')):
        if classify_model_role(row.get('name',''),row.get('source_category',''),row.get('model','')).role!=OWN_MODEL:
            reasons.append(label+'_OWN_MODEL_NOT_PROVEN')
        if not title_represents(row.get('name',''),clean_observed_model(row)['model'],row.get('brand','')):
            reasons.append(label+'_VISIBLE_EXECUTION_NOT_PROVEN')
    if source.get('card_kind') in {'MULTI_VARIANT_WITH_EXPLICIT_IDENTITIES','SERIES_OR_GROUP_CARD'}:
        reasons.append('PARENT_CARD_NOT_SINGLE_VARIANT')
    if source.get('semantic_conflicts') or source.get('match_conflicts'): reasons.append('UPSTREAM_FACTUAL_CONTRADICTION')
    left_class,right_class=execution_class_marks(source.get('name','')),execution_class_marks(target.get('name',''))
    if left_class and right_class and left_class.isdisjoint(right_class): reasons.append('EXPLICIT_EXECUTION_CLASS_CONTRADICTION')
    guards = {}
    for label,guard in (('axis',axis_execution_guard),('condition',condition_execution_guard)):
        guards[label]=guard(source.get('name',''),source.get('properties',{}),target.get('name',''),target.get('properties',{}))
        if guards[label]['blocked']: reasons.append(label.upper()+'_EXECUTION_GUARD_BLOCKED')
    typed=characteristic_evidence(source,target)
    if typed['contradictions']: reasons.append('SAME_ROLE_TYPED_CONTRADICTION')
    if not source.get('evidence_ref'): reasons.append('SOURCE_PROVENANCE_MISSING')
    path = 'P4_APPROVED_BRAND_ALIAS_EXACT_MODEL' if alias else (
        'P5_NAME_NORMALIZATION_EXACT_IDENTITY' if normalize_model(source.get('model',''),sb)!=normalize_model(target.get('model',''),tb) else (
        'P3_EXACT_VARIANT_IN_SERIES' if re.search(r'серия|series|—|\s/\s',target.get('name',''),re.I) else 'P2_EXACT_FULL_MODEL'))
    return {'confirmed':not reasons,'blocking_reasons':sorted(set(reasons)),
            'acceptance_path':path,'evidence':{'rule_version':RULE_VERSION,
            'source_model':source.get('model'),'canonical_model':target.get('model'),
            'normalized_source_model':sm,'normalized_canonical_model':tm,
            'source_brand':sb,'canonical_brand':tb,'scoped_identity_alias':alias,
            'source_kind':sk,'canonical_kind':tk,'execution_guards':guards,'characteristics':typed,
            'canonical_name_review': source.get('name')!=target.get('name')}}


class PolicyV2Index:
    def __init__(self, targets):
        self.targets=targets
        self.exact=defaultdict(set)
        for pid,t in targets.items():
            if t.get('active')=='Y' and usable_model(t.get('model',''),t.get('brand','')):
                self.exact[(normalize_brand(t.get('brand')),identity_model(t))].add(pid)

    def retrieve(self, row):
        brand,key=normalize_brand(row.get('brand')),identity_model(row)
        ids=set(self.exact.get((brand,key),()))
        for a,b,model in BRAND_IDENTITY_ALIASES:
            if key==model and brand in (a,b): ids.update(self.exact.get((b if brand==a else a,key),()))
        return sorted(ids)

    def evaluate(self,row):
        ids=self.retrieve(row)
        candidates=[{'sterbrust_product_id':pid,'name':self.targets[pid]['name'],**assess_pair(row,self.targets[pid])} for pid in ids]
        passing=[c for c in candidates if c['confirmed']]
        confirmed=passing[0] if len(passing)==1 else None
        reasons=sorted({r for c in candidates for r in c['blocking_reasons']})
        if len(passing)>1: reasons=['CANONICAL_IDENTITY_NOT_UNIQUE']
        if not ids: reasons=['NO_EXACT_CANONICAL_CANDIDATE_NOT_ABSENCE_PROOF']
        if confirmed: reasons=[]
        return {'rule_version':RULE_VERSION,'confirmed':confirmed,'candidates':candidates,
                'retrieval_ids':ids,'blocking_reasons':reasons,'alias_auto_accept':False,'fuzzy_auto_accept':False}


def kami_source_view(card,evidence_ref):
    from .kami_matching import source_view
    row=source_view(card,evidence_ref)
    row['card_kind']=card.raw_data.get('card_kind')
    if not row['model'] and row['card_kind']=='AMBIGUOUS':
        # Isolated single visible title can repair parser uncertainty, never a
        # parent/variant matrix and never a model borrowed from a target.
        variants=card.raw_data.get('variant_evidence',[])
        if len(variants)<=1:
            title=card.name
            if card.brand: title=re.sub(re.escape(card.brand),' ',title,flags=re.I)
            model=extract_model(title)
            if usable_model(model,card.brand) and title_represents(title,model): row['model']=row['execution']=model
    row['model_role']=classify_model_role(row['name'],row['source_category'],row['model']).role
    return row
