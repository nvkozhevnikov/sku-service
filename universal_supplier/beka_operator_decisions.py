"""Exact operator-authorized card equivalences; no global execution aliases."""
from copy import deepcopy
from dataclasses import replace
from sterbrust_matching.normalization import normalize_model
from .beka_visible_identity import RESOLVED,product_from_assessment

DECISIONS={
 'https://beka-mak.su/product/avtomaticheskiy_lentochnopilnyy_stanok_beka_mak_bmso_320gs_nc/':('BMSO-320GS NC','BMSO 320 GS NC +HTC'),
 'https://beka-mak.su/product/avtomaticheskiy_lentochnopilnyy_stanok_beka_mak_bmso_360ch_nc/':('BMSO-360CH NC','BMSO 360 CH NC +HTC+CC'),
 'https://beka-mak.su/product/avtomaticheskiy_lentochnopilnyy_stanok_beka_mak_bmso_440cdgs/':('BMSO-440CDGS','BMSO 440 CDGS NC'),
}
COOLANT='https://beka-mak.su/product/smazochno_okhlazhdayushchaya_zhidkost/'

def apply_decision(a):
    result=deepcopy(a)
    if a['url']==COOLANT:
        result.update(ledger_disposition='EXCLUDED_OPERATOR_INSUFFICIENT_IDENTITY',operator_provenance='OPERATOR-CONFIRMED 2026-10-01; this URL only, not a consumables ban')
        return result
    if a['url'] not in DECISIONS: return result
    primary,alias=DECISIONS[a['url']]
    if a['source']!='beka_mak' or normalize_model(a['visible_primary_model'])!=normalize_model(primary) or not a['visible_article'] or any(normalize_model(v)!=normalize_model(alias) for v in a['visible_article']):
        raise ValueError('Operator equivalence does not match saved visible fields')
    if set(a['reasons'])!={'VISIBLE_H1_ARTICLE_FULL_EXECUTION_DISAGREES'}:
        raise ValueError('Operator equivalence cannot clear another contradiction')
    result.update(verdict=RESOLVED,reasons=['EXACT_CARD_OPERATOR_CONFIRMED_VISIBLE_EQUIVALENCE'],
        operator_provenance='OPERATOR-CONFIRMED 2026-10-01',ledger_disposition='RESOLVED_OPERATOR_EQUIVALENCE')
    result['aliases'].append({'kind':'operator_confirmed_visible_alias','value':alias,
        'provenance':'visible article / OPERATOR-CONFIRMED 2026-10-01 / '+a['url'],
        'primary_model':primary,'use':'CARD_SCOPED_EQUIVALENCE_AND_CANDIDATE_RETRIEVAL_ONLY',
        'not_global_suffix_rule':True})
    return result

def operator_product(a,body):
    p=product_from_assessment(a,body)
    if a['url'] not in DECISIONS: raise ValueError('Only three explicit operator cards may be inserted')
    # Keep the actual visible article in evidence, not as an authoritative
    # canonical manufacturer model or an independent execution identity.
    return replace(p,manufacturer_article=None)
