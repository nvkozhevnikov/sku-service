import xml.etree.ElementTree as ET
import pytest
from universal_supplier.supplier_neutral_xml import neutral_snapshot_xml, neutral_new_groups_xml

def record():
    return {'supplier_code':'intervesp','external_id':'url-sha256:test','name':'Станок',
            'price_state':'price_on_request','price':None,'raw_data':{'source_content':{'description_text':'Описание'}}}

def test_utf8_null_price_and_no_invented_sterbrust_id():
    data=neutral_snapshot_xml([record()]); root=ET.fromstring(data)
    assert root.get('esol_import_payload')=='false'
    assert root.find('.//name').text=='Станок'
    assert root.find('.//price') is None
    assert root.find('.//sterbrust_product_id') is None

def test_supplier_namespace_uniqueness_and_numeric_price_guards():
    with pytest.raises(ValueError): neutral_snapshot_xml([record(),record()])
    with pytest.raises(ValueError): neutral_snapshot_xml([{**record(),'price':0}])
    with pytest.raises(ValueError): neutral_snapshot_xml([{**record(),'price_state':'numeric_public','price':1}])

def test_existing_id_and_new_group_are_distinct_fields():
    row=record()
    data=neutral_snapshot_xml([row],matching=[{'source':'intervesp','external_id':row['external_id'],
        'classification':'NEW_CANDIDATE','new_candidate_id':'NEW:test','new_group_id':'NEW_GROUP:test'}])
    root=ET.fromstring(data)
    assert root.find('.//new_group_id').text=='NEW_GROUP:test'
    assert root.find('.//sterbrust_product_id') is None
    with pytest.raises(ValueError): neutral_snapshot_xml([row],matching=[{'source':'intervesp',
        'external_id':row['external_id'],'classification':'EXISTING_CONFIRMED','sterbrust_product_id':'NEW:test'}])


def test_historical_price_is_not_promoted_to_current_public_or_sterbrust_price():
    root=ET.fromstring(neutral_snapshot_xml([{**record(), 'supplier_code':'optimum',
        'price_state':'historical_snapshot_not_current_commercial', 'historical_offer_price':'100',
        'historical_offer_currency':'RUB', 'source_freshness':'RESTORED_HISTORICAL_SNAPSHOT_NOT_LIVE'}]))
    assert root.find('.//price') is None
    historic=root.find('.//historical_offer_price')
    assert historic.text=='100'
    assert historic.get('price_role')=='historical_supplier_snapshot_unverified_current_role'


def test_one_create_group_has_two_supplier_sources_and_no_sterbrust_id():
    source=[record(),{**record(),'supplier_code':'beka_mak'}]
    rows=[{'source':r['supplier_code'],'external_id':r['external_id'],'classification':'NEW_CANDIDATE',
           'new_candidate_id':'NEWC:'+r['supplier_code'],'new_group_id':'NEWG:one','proposed_section_id':4004,
           'readiness':{'identity_ready':True,'full_ready':False}} for r in source]
    root=ET.fromstring(neutral_new_groups_xml(source,matching=rows))
    assert len(root.findall('canonical_new_group'))==1
    assert len(root.findall('.//source_product'))==2
    assert root.find('.//sterbrust_product_id') is None
    rows[1]['readiness']['identity_ready']=False
    assert not ET.fromstring(neutral_new_groups_xml(source,matching=rows)).findall('canonical_new_group')
