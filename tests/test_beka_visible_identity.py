import json
import pytest
from universal_supplier.beka_visible_identity import *

URL='https://www.bekamak.com/urun/bmsy-320-dg/en'
def page(h1='BMSY-320DGH',sku='BMSY-320-DG',extra=''):
    return '<h1>'+h1+'</h1><div id="teknik-detaylar"><table><tr><td>Blade Dimensions</td><td>4160x34x1.1 mm</td></tr>'+extra+'</table></div><script type="application/ld+json">'+json.dumps({'@type':'Product','url':URL,'sku':sku,'name':sku})+'</script>'
def check(body,url=URL,**kw): return assess('beka_mak_tr',url,body,approved_urls={url},**kw)

def test_url_and_hidden_sku_are_aliases_not_primary():
    a=check(page()); assert a['verdict']==RESOLVED
    p=product_from_assessment(a,page()); assert p.supplier_model=='BMSY-320DGH'
    assert p.manufacturer_article is None and p.site_internal_id=='bmsy-320-dg'
    assert {x['kind'] for x in a['aliases']}=={'url_alias','metadata_alias'}
    assert not a['canonical_identity_confirmed'] and not a['canonical_absence_proven']
    assert all(x['use']=='CANDIDATE_RETRIEVAL_ONLY' for x in a['aliases'])

@pytest.mark.parametrize('other',['BMSY-320DG','BMSY-320DGH NC','BMSY-320DGH +HTC'])
def test_visible_article_execution_conflict(other):
    a=check(page(extra='<tr><td>SKU</td><td>'+other+'</td></tr>'))
    assert a['verdict']==CONTRADICTION
    with pytest.raises(ValueError): product_from_assessment(a,page())

def test_hidden_article_is_not_visible():
    a=check(page(extra='<tr hidden><td>SKU</td><td>BMSY-999C</td></tr>'))
    assert a['verdict']==RESOLVED

def test_visible_model_characteristic_contradiction():
    assert check(page(extra='<tr><td>Model</td><td>BMSY-325DGH</td></tr>'))['verdict']==CONTRADICTION

def test_insufficient_not_rescued_by_metadata():
    assert check(page(h1='Product',sku='BMSY-320DGH'))['verdict']==INSUFFICIENT

def test_accessory_reference_not_machine():
    assert check(page(h1='BMSY-320DGH jaws'))['verdict']==CONTRADICTION

def test_operator404_not_absence_and_never_persist():
    a=check(page(),url=STALE_URL,operator_stale=True)
    assert a['verdict']==STALE and not a['canonical_absence_proven']
    with pytest.raises(ValueError): product_from_assessment(a,page())
    with pytest.raises(ValueError): check(page(),operator_stale=True)

def test_scope_fail_closed():
    with pytest.raises(ValueError): assess('intervesp',URL,page(),approved_urls={URL})
    with pytest.raises(ValueError): assess('beka_mak_tr',URL,page(),approved_urls=set())

def test_ru_p_saw_and_typographical_cyrillic_o():
    assert normalize_model(model('BMSО 230E'))==normalize_model(model('BMSO 230 E'))
    assert normalize_model(model('P-SAW 330CS'))==normalize_model(model('P-SAW 330 CS'))
    assert normalize_model(model('BMSO 230E'))!=normalize_model(model('BMSO 230E NC'))

def test_own_kind_missing_is_review():
    assert check('<h1>BMSY-320DGH</h1>')['verdict']==INSUFFICIENT

def test_saved29_scope_regression():
    from scripts.audit_beka_visible_identity import inputs
    from pathlib import Path
    rows=inputs(); scope={r['url'] for r in rows}
    result=[assess(r['source'],r['url'],Path(r['capture_path']).read_text(encoding='utf-8'),approved_urls=scope,operator_stale=r['url']==STALE_URL) for r in rows]
    from collections import Counter
    assert Counter(a['verdict'] for a in result)=={RESOLVED:24,CONTRADICTION:3,INSUFFICIENT:1,STALE:1}
    assert sum(a['verdict']==RESOLVED for a,r in zip(result,rows) if r['origin']=='HUMAN')==19

def test_scoped_bilingual_view_preserves_raw_identity_and_aliases():
    from scripts.match_beka_visible_identity import visible_context
    raw={'supplier_code':'beka_mak_tr','name':'BMSY-320DGH','raw_data':{
        'source_identity':{'raw_model':'BMSY-320DGH'},
        'source_content':{'source_category':'Band saw','technical_properties':[['Blade Dimensions','4160x34x1.1 mm']]}}}
    import copy
    before=copy.deepcopy(raw); view=visible_context(raw)
    assert raw==before and view['name']==raw['name']
    assert view['raw_data']['source_identity']==raw['raw_data']['source_identity']
    assert 'ленточнопильный станок' in view['raw_data']['source_content']['source_category']

def test_current_proposal_keeps_prior1802_and_never_confirms_alias_only():
    from scripts.audit_beka_visible_identity import load,OUT,PRIOR
    current=load(OUT/'CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json')['rows']
    old=load(PRIOR/'MATCHING_AFTER.json')['rows']
    assert current[:len(old)]==old
    assert len(current)==1826
    assert all(r['classification']=='REVIEW' and not r['sterbrust_product_id'] for r in current[len(old):])
    aliases=load(OUT/'CANONICAL_VISIBLE_CONTEXT/ALIAS_RETRIEVAL_AUDIT.json')
    assert all(not a['auto_confirmation_authority'] for a in aliases)
