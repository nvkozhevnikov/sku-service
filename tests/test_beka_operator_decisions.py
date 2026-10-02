import pytest
from copy import deepcopy
from universal_supplier.beka_operator_decisions import *
from scripts.audit_beka_visible_identity import load,OUT

@pytest.mark.parametrize('url',list(DECISIONS))
def test_exact_operator_equivalence_preserves_raw_fields(url):
    a=next(x for x in load(OUT/'SOURCE_IDENTITY_AUDIT.json') if x['url']==url); before=deepcopy(a)
    r=apply_decision(a)
    assert a==before and r['verdict']==RESOLVED
    assert r['visible_primary_model']==a['visible_primary_model']
    assert r['visible_article']==a['visible_article']
    assert r['aliases'][-1]['not_global_suffix_rule']
    assert not r['canonical_identity_confirmed']
    bad=deepcopy(a); bad['visible_article']=['BMSO-999NC']
    with pytest.raises(ValueError): apply_decision(bad)
    bad=deepcopy(a); bad['reasons'].append('ACCESSORY_MACHINE_IDENTITY_CONTEXT')
    with pytest.raises(ValueError): apply_decision(bad)

def test_unrelated_card_not_relaxed_and_coolant_exclusion_specific():
    a=next(x for x in load(OUT/'SOURCE_IDENTITY_AUDIT.json') if x['url']==COOLANT)
    assert apply_decision(a)['ledger_disposition']=='EXCLUDED_OPERATOR_INSUFFICIENT_IDENTITY'
    other={**a,'url':'https://beka-mak.su/product/another-consumable/'}
    assert apply_decision(other)==other

def test_operator_ledger_closed_without_global_suffix_change():
    from scripts.close_beka_operator_ledger import prepare
    audits,pairs=prepare()
    assert len(audits)==29 and len(pairs)==3
    assert sum(a['verdict']==RESOLVED for a in audits)==27
    assert all(p.manufacturer_article is None for p,c in pairs)
    assert len({p.site_internal_id for p,c in pairs})==3
    assert normalize_model('BMSO-440CDGS')!=normalize_model('BMSO-440CDGS NC')
