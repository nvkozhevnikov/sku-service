"""Deterministic kind-evidence extension mined from frozen V2.1 REVIEW cohorts.

No AI score, I/O, accepted-ID assignment, global alias or source mutation.
V2.1 defaults remain unchanged. Every new acceptance reruns its exact full-model,
visible execution, brand, own-model, axis, condition and typed contradiction gates.
"""
from collections import Counter
import re
from .matching_policy_v2 import PolicyV2Index, assess_pair, sold_kind, title_represents
from sterbrust_matching.normalization import normalize_text

RULE_VERSION='matching-policy-v2.2-kind-evidence-2026-10-09'
# Primary components must precede a complete-machine phrase to block it. A
# machine equipped "with controller" is different from "controller for machine".
PART=re.compile(r'контроллер|\bcontroller\b|ручк\w*|\bhandle\b|губк\w*|\bjaws?\b|'
    r'цанг\w*|\bcollet\b|патрон\w*|\bchuck\b|резец|резцы|\btool ?bit\b|'
    r'оснастк\w*|принадлежност\w*|комплект\w*|запчаст\w*|\bspare part\b|'
    r'модул\w*|\bmodule\b|панел\w*|\bpanel\b|кабел\w*|\bcable\b|'
    r'держател\w*|\bholder\b|кронштейн\w*|\bbracket\b|стойк\w*|'
    r'вращающ\w* центр|неподвижн\w* центр|\blive cent(?:er|re)\b|\bdead cent(?:er|re)\b')
HEADS=(
 ('lathe',r'токарн\w*(?:\s+[\w-]+){0,3}\s+(?:станок|центр)|автомат\w* продольного точения|\bturning cent(?:er|re)\b'),
 ('milling_machine',r'фрезерн\w*(?:\s+[\w-]+){0,3}\s+(?:станок|центр)|обрабатывающ\w* центр|\bmachining cent(?:er|re)\b'),
 ('bandsaw',r'ленточно[\s-]*пильн\w*|\bband\s*saw\b'),
 ('saw_welder',r'аппарат\w* для сварки ленточных пил'),
 ('hydraulic_press',r'гидравлическ\w*(?:\s+[\w-]+){0,3}\s+пресс|\bhydraulic press\b'),
 ('shear',r'гильотинн\w* ножниц\w*|\bguillotine shear\b'),
)

def kind_proof(row, *, allow_category=True):
    title=normalize_text(row.get('name',''));category=normalize_text(row.get('source_category',''))
    old=sold_kind(row);part=PART.search(title)
    matches=[(m.start(),kind,m.group()) for kind,pattern in HEADS for m in re.finditer(pattern,title)]
    # Saw welding equipment mentions blades, but sells the welder, not a saw.
    weld=next((x for x in matches if x[1]=='saw_welder'),None)
    if weld:matches=[weld]
    if matches:
        position,kind,phrase=min(matches,key=lambda x:x[0])
        if part and part.start()<position:
            return {'kind':'accessory','rule_id':'V22_PRIMARY_COMPONENT_HEAD_BLOCK','positive':[],
                    'negative':[part.group()], 'old_kind':old}
        # Generic machining-centre wording cannot flatten an explicit turning
        # or turn-mill execution into a generic milling machine.
        if kind=='milling_machine' and re.search(r'токарно[\s-]*фрезер|\bturn.?mill\b',title):
            kind='turn_mill_machine'
        elif kind=='milling_machine' and re.search(r'токарн|\bturning\b',title):kind='lathe'
        elif kind=='milling_machine' and re.search(r'лазерн|\blaser\b',title+' '+category):kind='laser_machine'
        elif kind=='milling_machine' and re.search(r'электроэрозион|\bedm\b',title+' '+category):kind='edm_machine'
        elif kind=='milling_machine' and re.search(r'сверлильн|\bdrilling\b',title) and not re.search(r'фрезерн|\bmilling\b',title):kind='drilling_machine'
        return {'kind':kind,'rule_id':'V22_EXPLICIT_COMPLETE_MACHINE_HEAD',
                'positive':[{'field':'visible_product_title','value':phrase}], 'negative':[], 'old_kind':old}
    # Category evidence only refines unknown/generic/saw type. Explicit parts,
    # service, other named equipment and contrary categories are never overridden.
    if allow_category and old in ('unknown','machine','saw') and not part:
        band=re.search(r'ленточно[\s-]*пильн\w*|\bband\s*saw\b',category)
        excluded=re.search(r'полотн|\bblades?\b|аксессуар|оснастк|запчаст|\bparts?\b|\baccessor',category)
        explicit_other=re.search(r'дисков\w* пил|\bcircular saw\b|сварк|\bweld',title)
        # Require explicit own visible execution; category alone never supplies
        # model, manufacturer, execution or a canonical candidate.
        own=title_represents(row.get('name',''),row.get('model',''),row.get('brand',''))
        if band and not excluded and not explicit_other and own:
            return {'kind':'bandsaw','rule_id':'V22_EXPLICIT_BANDSAW_CATEGORY',
                    'positive':[{'field':'visible_source_category','value':band.group()},
                                {'field':'visible_execution','value':row.get('model')}],
                    'negative':[], 'old_kind':old}
    if part and old not in ('controller','handle','jaw','chuck_jaw'):
        return {'kind':'accessory','rule_id':'V22_PRIMARY_COMPONENT_HEAD_BLOCK','positive':[],
                'negative':[part.group()], 'old_kind':old}
    return {'kind':old,'rule_id':None,'positive':[],'negative':[], 'old_kind':old}

def automation_mode(title):
    text=normalize_text(title)
    # Auxiliary ATC/lubrication is not a declaration of main operating mode.
    text=re.sub(r'с автоматическ\w* (?:смен\w* инструмента|смазк\w*)|\bautomatic tool chang\w*', '', text)
    # Longer words first. Do not equate semi-automatic with automatic by substring.
    if re.search(r'полуавтомат|\bsemi[ -]?automatic\b',text):return 'SEMI_AUTOMATIC'
    if re.search(r'(?<!полу)автомат|\bautomatic\b',text):return 'AUTOMATIC'
    if re.search(r'\bручн\w*|\bmanual\b',text):return 'MANUAL'
    return None

def automation_context(row):
    title=automation_mode(row.get('name',''))
    category=automation_mode(row.get('source_category',''))
    return {'title':title,'category':category,'mode':title or category,
            'self_contradiction':bool(title and category and title!=category)}

def structural_marks(title):
    text=normalize_text(title)
    orientation={code for code,pattern in [('HORIZONTAL',r'горизонтальн|\bhorizontal\b'),
                                           ('VERTICAL',r'вертикальн|\bvertical\b')] if re.search(pattern,text)}
    frame={code for code,pattern in [('TWO_COLUMN',r'двухколон|\b(?:two|double)[ -]?column'),
                                    ('ONE_COLUMN',r'одноколон|\b(?:one|single)[ -]?column'),
                                    ('CANTILEVER',r'маятников|консольн|\bcantilever\b|\bpendulum\b')] if re.search(pattern,text)}
    return {'orientation':sorted(orientation),'frame':sorted(frame)}

def assess_pair_v22(source,target, *, allow_category=True):
    base=assess_pair(source,target)
    proofs=[kind_proof(row,allow_category=allow_category) for row in (source,target)]
    primary_component=any(p['rule_id']=='V22_PRIMARY_COMPONENT_HEAD_BLOCK' for p in proofs)
    modes=[automation_mode(r.get('name','')) for r in (source,target)]
    mode_conflict=all(modes) and modes[0]!=modes[1]
    if base['confirmed'] and not primary_component and not mode_conflict:
        return {**base,'rule_id':'V21_RETAINED','new_rule_applied':False}
    result=assess_pair(source,target,kind_resolver=lambda row:kind_proof(row,allow_category=allow_category)['kind'])
    rules=sorted({p['rule_id'] for p in proofs if p['rule_id'] and p['kind']!=p['old_kind']})
    result['evidence']['kind_evidence']=proofs
    result['evidence']['rule_version']=RULE_VERSION
    result.update(rule_id='+'.join(rules) if rules else 'V21_UNRESOLVED',new_rule_applied=bool(rules))
    if result['confirmed'] and mode_conflict:
        result['confirmed']=False
        result['blocking_reasons'].append('V22_EXPLICIT_AUTOMATION_CONTRADICTION')
        result['rule_id']='V22_EXPLICIT_AUTOMATION_CONTRADICTION'
    contexts=[automation_context(r) for r in (source,target)]
    if result['confirmed'] and (any(c['self_contradiction'] for c in contexts) or
            all(c['mode'] for c in contexts) and contexts[0]['mode']!=contexts[1]['mode']):
        result['confirmed']=False
        result['blocking_reasons'].append('V22_EXPLICIT_CATEGORY_AUTOMATION_CONTRADICTION')
    structures=[structural_marks(r.get('name','')) for r in (source,target)]
    for key in ('orientation','frame'):
        if result['confirmed'] and structures[0][key] and structures[1][key] and set(structures[0][key]).isdisjoint(structures[1][key]):
            result['confirmed']=False
            result['blocking_reasons'].append('V22_EXPLICIT_'+key.upper()+'_CONTRADICTION')
    uncertain=result['evidence']['characteristics']['uncertain']
    if result['confirmed'] and any(p['reason']=='blade_vector_component_order_not_proven' for p in uncertain):
        result['confirmed']=False
        result['blocking_reasons'].append('V22_DIFFERENT_BLADE_VECTOR_REQUIRES_ORDER_EVIDENCE')
    if result['confirmed'] and not rules:
        raise AssertionError('No new evidence rule may silently change V2.1 acceptance')
    result['evidence']['negative_guard_result']={'blocking_reasons':list(result['blocking_reasons']),
        'automation_modes':modes,'automation_context':contexts,'all_current_guards_pass':result['confirmed']}
    result['evidence']['negative_guard_result']['structural_execution']=structures
    return result

class PolicyV22Index(PolicyV2Index):
    def __init__(self,targets,*,allow_category=True):
        if type(allow_category) is not bool:raise TypeError('Rule profile requires explicit boolean')
        super().__init__(targets)
        self.allow_category=allow_category

    def evaluate(self,row):
        ids=self.retrieve(row)
        candidates=[{'sterbrust_product_id':pid,'name':self.targets[pid]['name'],
                     **assess_pair_v22(row,self.targets[pid],allow_category=self.allow_category)} for pid in ids]
        passing=[c for c in candidates if c['confirmed']]
        chosen=passing[0] if len(passing)==1 else None
        reasons=sorted({r for c in candidates for r in c['blocking_reasons']})
        if len(passing)>1:reasons=['CANONICAL_IDENTITY_NOT_UNIQUE']
        if not ids:reasons=['NO_EXACT_CANONICAL_CANDIDATE_NOT_ABSENCE_PROOF']
        if chosen:reasons=[]
        return {'rule_version':RULE_VERSION,'confirmed':chosen,'candidates':candidates,
                'retrieval_ids':ids,'blocking_reasons':reasons,'alias_auto_accept':False,'fuzzy_auto_accept':False}
