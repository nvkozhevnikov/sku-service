"""Explicit axis execution evidence for proposals, never inferred from models."""
import re
from sterbrust_matching.normalization import normalize_text


def axis_signature(name, properties=None):
    text=normalize_text(name)
    axes=set(re.findall(r'\b([3-9])\s*(?:[- ]?\s*axis\b|[- ]?\s*осев\w*|\s*ос[ьи]\b)',text))
    for word,number in (('трехосев','3'),('трёхосев','3'),('четырехосев','4'),
                        ('четырёхосев','4'),('пятиосев','5'),('шестиосев','6')):
        if word in text: axes.add(number)
    interpolation=set(re.findall(r'\b([34]\s*\+\s*[12])\b',text))
    for value in interpolation: axes.add(str(sum(map(int,re.findall(r'\d',value)))))
    for key,value in (properties or {}).items():
        if re.fullmatch(r'(?:число|количество) осей(?: чпу)?|axes(?: count)?',normalize_text(key)):
            if re.fullmatch(r'[3-9]',str(value).strip()): axes.add(str(value).strip())
    return {'axes':sorted(axes),'interpolation':sorted(v.replace(' ','') for v in interpolation)}


def axis_execution_guard(left_name,left_properties,right_name,right_properties):
    left=axis_signature(left_name,left_properties)
    right=axis_signature(right_name,right_properties)
    if any(len(value)>1 for side in (left,right) for value in side.values()):
        return {'blocked':True,'contradiction':True,'reason':'conflicting_explicit_axis_evidence','source':left,'target':right}
    if left==right: return {'blocked':False,'source':left,'target':right}
    contradicted=any(left[key] and right[key] and left[key]!=right[key] for key in left)
    return {'blocked':True,'contradiction':bool(contradicted),
            'reason':'axis_execution_contradiction' if contradicted else 'axis_execution_evidence_incomplete',
            'source':left,'target':right}


def condition_signature(name, properties=None):
    """Do not interpret a factory model suffix NEW as product condition."""
    values = [str(name)] + [str(value) for key,value in (properties or {}).items()
                           if normalize_text(key) in ('состояние','condition')]
    text=normalize_text(' '.join(values))
    if re.search(r'\bб\s*/\s*у\b|бывш\w*\s+в\s+употреблен|\bused\s+(?:machine|equipment)\b',text):
        return 'used'
    if re.search(r'\bнов(?:ый|ое)\s+(?:станок|оборудование)\b',text): return 'new'
    return 'unspecified'


def condition_execution_guard(left_name,left_properties,right_name,right_properties):
    left=condition_signature(left_name,left_properties)
    right=condition_signature(right_name,right_properties)
    blocked=(left!=right and 'used' in (left,right))
    return {'blocked':blocked,'contradiction':blocked and 'unspecified' not in (left,right),
            'reason':'used_condition_contradiction' if blocked and 'unspecified' not in (left,right)
                     else 'used_condition_evidence_incomplete' if blocked else '',
            'source':left,'target':right}
