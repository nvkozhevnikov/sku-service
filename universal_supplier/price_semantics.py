"""Business price type needs an explicit, price-scoped witness, not a numeric amount."""
from dataclasses import replace
import re

TYPES=frozenset({'RRP','retail','wholesale','dealer','promo','unknown'})


def price_type_proof(label='',*,source_path='',declared=None):
    raw=' '.join(str(label or '').split())
    matches=set()
    if declared in TYPES-{'unknown'}:matches.add(declared)
    for kind,pattern in (
        ('RRP',r'\b(?:РРЦ|RRP|рекомендованн\w*\s+розничн\w*\s+цен\w*)\b'),
        ('retail',r'\bрозничн\w*\s+цен\w*\b'),
        ('wholesale',r'\bоптов\w*\s+цен\w*\b'),
        ('dealer',r'\bдилерск\w*\s+цен\w*\b'),
        ('promo',r'\b(?:акционн\w*|промо)\s*цен\w*\b')):
        if re.search(pattern,raw,re.I):matches.add(kind)
    # The phrase "recommended retail price" is one RRP witness, not two types.
    if 'RRP' in matches and re.search(r'рекомендованн',raw,re.I):matches.discard('retail')
    return {'classification':next(iter(matches)) if len(matches)==1 else 'unknown',
        'status':'EXPLICIT' if len(matches)==1 else 'AMBIGUOUS' if matches else 'NOT_STATED',
        'raw_label':raw,'source_path':source_path,'rule':'explicit-price-type/v1',
        'not_inferred_from_public_numeric':True}


def typed_extraction(extraction,label='',*,source_path='',declared=None):
    proof=price_type_proof(label,source_path=source_path,declared=declared)
    return replace(extraction,price_type=proof['classification'],price_type_evidence=proof)


def observation_price_type(row):
    proof=(row.get('extraction_evidence') or {}).get('business_price_type') or {}
    if (proof.get('status')=='EXPLICIT' and proof.get('rule')=='explicit-price-type/v1' and
            proof.get('source_path') and proof.get('classification') in TYPES-{'unknown'}):
        return proof['classification']
    return row.get('observed_price_type') if row.get('observed_price_type') in TYPES else 'unknown'
