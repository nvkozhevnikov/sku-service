"""Read-only XML coverage: partial fields, durable identity and real NEW staging."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from xml.etree import ElementTree as ET
from pathlib import Path
import json
import pytest
from test_automation_system import snapshot,NOW
from universal_supplier.dual_preview import build,publish,latest
from universal_supplier.control_plane.admin_store import canonical_fingerprint,identity_snapshot


@pytest.mark.parametrize('price,stock,tags',[
    (None,'in_stock',{'availability'}),(0,'in_stock',{'availability'}),
    (Decimal('2.30'),'unknown',{'price','currency'}),
    (None,'unknown',set())])
def test_one_trusted_field_does_not_depend_on_the_other(price,stock,tags):
    value=snapshot();row=value['records'][0];row.update(observed_price=price,observed_availability=stock)
    raw,m=build(value);product=ET.fromstring(raw['updates']).find('product')
    assert (product is not None)==bool(tags)
    if product is not None:
        assert {n.tag for n in product}==tags
        assert product.get('sterbrust_product_id')=='10001'
        if 'price' in tags:assert product.findtext('price')=='2.30'
    assert m['counts']['updates']==bool(tags)


@pytest.mark.parametrize('price_ttl,stock_ttl,tags',[(60,3600,{'availability'}),(3600,60,{'price','currency'})])
def test_stale_field_is_omitted_without_extending_ttl(price_ttl,stock_ttl,tags):
    value=snapshot()
    from universal_supplier.control_plane.supplier_automation import VERSION
    known=next(s for s in value['suppliers'] if s['code']==value['records'][0]['supplier_code'])
    known['crawl_settings']['automation']={'version':VERSION,'price_ttl':price_ttl,'availability_ttl':stock_ttl}
    before=deepcopy(value);raw,m=build(value)
    assert {n.tag for n in ET.fromstring(raw['updates']).find('product')}==tags
    assert value==before


def test_committed_card_does_not_wait_for_global_full_crawl_or_materialized_selection():
    value=snapshot();value['coverage']={'completed_phases':4,'total_phases':22}
    value['offer_candidates'][11]=[replace(value['offer_candidates'][11][0],latest_successful_full_crawl_at=None)]
    row=value['records'][0];row['last_selection_at']=None;row['matching_current_for_capture']=False
    raw,m=build(value)
    assert m['counts']['updates']==1 and m['selection_plan']['scope']=='READ_ONLY_NOT_PERSISTED'
    assert row['confirmed_sterbrust_id']=='10001' and not row['matching_current_for_capture']


def staging(row):
    row.update(blocking_review=True,review_lifecycle='OPEN',review_automatic_decision='SAFE_NEW_PRODUCT_CANDIDATE',latest_manual_decision=None)
    row['review_identity_fingerprint']=canonical_fingerprint(identity_snapshot({**row,'supplier':row['supplier_code']}))


def test_new_staging_case_and_disabled_collection_are_not_identity_review():
    value=snapshot();row=value['records'][1];staging(row)
    value['suppliers'][next(i for i,s in enumerate(value['suppliers']) if s['code']==row['supplier_code'])]['enabled']=False
    before=deepcopy(value);raw,m=build(value)
    assert m['counts']['new_products']==1 and value==before
    node=ET.fromstring(raw['new_products']).find('product')
    assert node.find('preview_readiness').get('requires_operator_review')=='true'
    assert node.find('preview_readiness').get('import_allowed')=='false'
    assert node.find('preview_readiness').get('collection_enabled')=='false'
    assert row['blocking_review'] and row['review_lifecycle']=='OPEN'


@pytest.mark.parametrize('change',[
    {'review_automatic_decision':'REVIEW_EXISTING'}, {'review_lifecycle':'REOPENED_SOURCE_CHANGED'},
    {'latest_manual_decision':'MANUAL_REJECTED'}, {'review_identity_fingerprint':'wrong'},
    {'matching_current_for_capture':False},{'match_conflicts':{'power':'CONTRADICTION'}},
    {'product_kind':'accessory'},{'product_kind':'unknown'},{'extraction_evidence':{'variant_or_group':True}},
    {'own_model':'BF 20 L Vario NC','brand_raw':None},
])
def test_new_preview_does_not_bypass_identity_review_or_operator_locks(change):
    value=snapshot();row=value['records'][1];staging(row);row.update(change)
    assert build(value)[1]['counts']['new_products']==0


def test_receipt_region_or_capture_mismatch_still_blocks_both_fields():
    value=snapshot();value['records'][0]['receipt_identity_valid']=False
    assert build(value)[1]['counts']['updates']==0


def test_reference_only_offer_cannot_block_a_valid_commercial_source_for_same_id():
    value=snapshot();other=deepcopy(value['records'][0]);other.update(source_product_id=103,
        supplier_code='beka_mak_tr',external_id='ref-1',offer_id=3,capture_id=3)
    value['records'].append(other)
    candidate=replace(value['offer_candidates'][11][0],offer_id=3,source_product_id=103,supplier_code='beka_mak_tr')
    value['offer_candidates'][11].append(candidate)
    assert build(value)[1]['counts']['updates']==1


def test_quarantine_is_scoped_to_one_source_not_all_offers_for_canonical_id():
    value=snapshot();row=value['records'][0];row['blocking_quarantine']=True
    other=deepcopy(row);other.update(blocking_quarantine=False,source_product_id=104,
        supplier_code='optimum',external_id='other',offer_id=4,capture_id=4)
    value['records'].append(other)
    value['offer_candidates'][11].append(replace(value['offer_candidates'][11][0],
        offer_id=4,source_product_id=104,supplier_code='optimum'))
    raw,m=build(value)
    assert m['counts']['updates']==1 and m['selection_trace'][0]['supplier']=='optimum'


def test_latest_quantity_contradiction_still_uses_native_selection_review():
    value=snapshot();value['offer_candidates'][11][0]=replace(value['offer_candidates'][11][0],quantity=Decimal('0'))
    assert build(value)[1]['counts']['updates']==0


def test_all_reasons_primary_and_source_totals_reconcile():
    value=snapshot();value['records'][0].update(blocking_review=True,blocking_quarantine=True,http_status=503)
    raw,m=build(value)
    row=next(r for r in m['record_trace']['updates'] if r['source_product_id']==101)
    assert {'CURRENT_REVIEW_OR_CONFLICT','UNRESOLVED_SOURCE_QUARANTINE','NO_FRESH_COMMERCIAL_FIELD'}<=set(row['reasons'])
    for kind in ('updates','new_products'):
        for source,counts in m['coverage_by_supplier'][kind].items():
            assert counts['candidates']==counts['eligible']+sum(counts['primary_reasons'].values())
            assert counts['reconciled']


def test_positive_to_zero_requires_per_record_explanation_and_not_ready(tmp_path):
    value=snapshot();raw,baseline=build(value);publish(tmp_path,raw,baseline)
    value['records'][0]['observed_at']=NOW-timedelta(days=5)
    value['records'][1]['observed_at']=NOW-timedelta(days=5)
    raw,without=build(value)
    assert without['quality_gate']['status']=='NOT_READY'
    # Publisher now loads its own baseline and derives row explanations, even if
    # the caller omitted build(baseline=...). Empty attempts never replace it.
    publish(tmp_path,raw,without)
    assert latest(tmp_path)['counts']=={'updates':1,'new_products':1}
    raw,m=build(value,baseline=baseline)
    assert len(m['quality_gate']['regressions'])==2 and m['quality_gate']['release_blocked']
    assert all(r['all_previous_records_explained'] for r in m['quality_gate']['regressions'])
    publish(tmp_path,raw,m)
    assert latest(tmp_path)['counts']=={'updates':1,'new_products':1}
    assert latest(tmp_path,attempt=True)['quality_gate']['business_acceptance']=='NOT_READY'


def test_legacy_stage5_117_compatible_rows_are_not_reinterpreted_as_fresh_data():
    from test_stage5 import row as legacy_row,snapshot as legacy_snapshot,POLICY
    from universal_supplier.yml_feed import build_yml
    rows=[]
    for i in range(117):
        rows.append(legacy_row(str(88585+i),catalog_product_id=100+i,selected_offer_id=200+i,
            offer_id=200+i,source_product_id=300+i,source_catalog_product_id=100+i,
            current_match_catalog_product_id=100+i,supplier_external_id=str(297+i)))
    artifact=build_yml(legacy_snapshot(*rows),POLICY)
    assert len(ET.fromstring(artifact.data).findall('.//offers/offer'))==117
    assert artifact.manifest['exported_offer_count']==117


def test_nonempty_new_preview_does_not_mark_empty_updates_business_ready():
    value=snapshot();value['records'][0]['observed_at']=NOW-timedelta(days=5)
    raw,m=build(value)
    assert m['counts']=={'updates':0,'new_products':1}
    assert m['quality_gate']['status']=='PARTIAL_NOT_READY'
    assert m['quality_gate']['streams']['updates']=='NOT_READY'
    assert m['quality_gate']['release_blocked'] and m['trusted_current_for_import'] is False


@pytest.mark.parametrize('change,expected',[
    ({},True),({'audited_at':'2020-01-01T00:00:00Z'},True),
    ({'match_last_validated_at':'2026-10-25T00:00:00Z'},False),
    ({'decision':'REVIEW_EXISTING'},False),({'source_last_success_at':None},False),
])
def test_validation_receipt_not_materialization_clock_proves_current_matching(change,expected):
    from universal_supplier.preview_store import matching_validation_is_current
    value={'match_status':'NEW_CANDIDATE','decision':'SAFE_NEW_PRODUCT_CANDIDATE',
        'source_last_success_at':'2026-10-26T12:00:00+03:00','match_last_validated_at':'2026-10-26T10:00:00Z',
        'audited_at':'2026-10-20T00:00:00Z'}
    value.update(change)
    assert matching_validation_is_current(value) is expected


def test_truncated_stored_model_cannot_merge_distinct_execution_titles():
    value=snapshot();row=value['records'][1]
    other=deepcopy(row);other.update(source_product_id=103,offer_id=3,capture_id=3,
        supplier_code='vekprom',external_id='103',name=row['name']+' NC')
    value['records'].append(other)
    raw,m=build(value)
    assert m['counts']['new_products']==0
    assert m['exclusion_counts']['new_products']['DIFFERENT_SOURCE_TITLES_REQUIRE_EXECUTION_REVIEW']==2


REAL=json.loads((Path(__file__).parent/'fixtures/xml_coverage_recovery_20261027.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('record',REAL['NEW20'],ids=lambda r:str(r['source_product_id']))
def test_saved_real_new_revalidation_and_content_receipts(record):
    from universal_supplier.preview_store import matching_validation_is_current
    from universal_supplier.commercial_freshness import assess,timestamp
    from universal_supplier.control_plane.supplier_automation import settings_for
    assert matching_validation_is_current(record) is True
    assert record['match_status']=='NEW_CANDIDATE' and record['confirmed_sterbrust_id'] is None
    states=assess(record,settings_for({}),timestamp(REAL['as_of']))
    assert states['receipt_valid'] and states['content']['state']=='fresh'
    # Historical commercial fields are deliberately not promoted to current.
    assert states['price']['state']!='fresh' and states['availability']['state']!='fresh'


@pytest.mark.parametrize('record',REAL['confirmed20'],ids=lambda r:str(r['source_product_id']))
def test_saved_real_confirmed_identity_survives_unknown_or_stale_commercial_data(record):
    from universal_supplier.commercial_freshness import assess,timestamp
    from universal_supplier.control_plane.supplier_automation import settings_for
    before=deepcopy(record)
    assert record['identity_confirmed'] and record['confirmed_sterbrust_id']
    states=assess(record,settings_for({}),timestamp(REAL['as_of']))
    assert states['price']['state']!='fresh' and states['availability']['state']!='fresh'
    assert record==before
