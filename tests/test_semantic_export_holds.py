from copy import deepcopy
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest
from tests.test_automation_system import snapshot
from tests.test_stage5 import row, POLICY
from universal_supplier.dual_preview import build, publish, latest
from universal_supplier.export_continuity import validate_payload
from universal_supplier.export_eligibility import HOLDS, semantic_export_hold
from universal_supplier.yml_feed import selected_row_violations


@pytest.mark.parametrize('source_id,external,reason', HOLDS)
def test_fresh_critical_offer_excluded_without_remapping_or_changing_snapshot(source_id,external,reason):
    data=snapshot();data['records'][0].update(source_product_id=source_id,external_id=external)
    data['offer_candidates'][11][0]=replace(data['offer_candidates'][11][0],source_product_id=source_id)
    before=deepcopy(data)
    xml,manifest=build(data)
    assert manifest['counts']['updates']==0
    trace=next(t for t in manifest['record_trace']['updates'] if t['source_product_id']==source_id)
    assert trace['semantic_hold']['reason']==reason and 'SEMANTIC_EXPORT_HOLD' in trace['reasons']
    assert manifest['freshness'][1]['price']['state']=='fresh'
    assert data==before and data['records'][0]['confirmed_sterbrust_id']=='10001'


def test_other_supplier_same_canonical_wins_and_history_remains_readable(tmp_path):
    data=snapshot();old,baseline=build(data);publish(tmp_path,old,baseline)
    held=data['records'][0];held.update(source_product_id=15,external_id='309')
    data['offer_candidates'][11][0]=replace(data['offer_candidates'][11][0],source_product_id=15)
    other={**held,'supplier_code':'optimum','source_product_id':999,'external_id':'unblocked','offer_id':3}
    other['supplier_id']=data['records'][1]['supplier_id']
    data['records'].append(other)
    data['offer_candidates'][11].append(replace(data['offer_candidates'][11][0],source_product_id=999,
        offer_id=3,supplier_code='optimum',supplier_id=other['supplier_id'],offer_supplier_id=other['supplier_id']))
    xml,m=build(data)
    assert m['counts']['updates']==1 and m['selection_trace'][0]['supplier']=='optimum'
    assert ET.fromstring(xml['updates']).find('product').get('sterbrust_product_id')=='10001'
    assert latest(tmp_path,'updates')==old['updates']  # no regeneration of historical files


def test_serialization_rejects_a_forged_fresh_held_trace():
    xml,m=build(snapshot());m['selection_trace'][0].update(source_product_id=15,external_id='309')
    with pytest.raises(ValueError,match='SEMANTIC_EXPORT_HOLD'):validate_payload(xml,m)


def test_stage5_guard_is_supplier_offer_scoped_and_never_canonical_id_scoped():
    held=row(source_product_id=15,supplier_code='partner_st',supplier_external_id='309')
    assert any('SEMANTIC_EXPORT_HOLD' in r for r in selected_row_violations([held],POLICY,1))
    assert semantic_export_hold(replace(held,supplier_code='optimum')) is None
    assert semantic_export_hold(replace(held,source_product_id=999,supplier_external_id='other')) is None
