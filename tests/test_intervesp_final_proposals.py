from copy import deepcopy
import pytest
from universal_supplier.intervesp_final_proposals import (
    assert_enrichment_complete, apply_detail_quarantine, terminal_bucket, final_counts, scoped_group_anchors,
    preserve_unresolved_reviews,
)
from universal_supplier.characteristic_evidence import equipment_scope
from scripts.enrich_intervesp_details import reviewed_timeout_continuation


def test_partial_duplicate_or_stopped_checkpoint_is_not_final():
    good = {'stage': 'DETAIL_ENRICHMENT_COMPLETE', 'plan_count': 1,
            'rows': [{'url': 'https://fixture.invalid/one', 'status': 'PERSISTED_ENRICHMENT'}]}
    assert assert_enrichment_complete(good, ['https://fixture.invalid/one'])
    for change in ({'stage': 'RUNNING'}, {'plan_count': 2}, {'rows': good['rows'] * 2},
                   {'rows': [{'url': 'https://fixture.invalid/one', 'status': 'NETWORK_ERROR'}]}):
        with pytest.raises(ValueError): assert_enrichment_complete({**good, **change}, ['https://fixture.invalid/one'])


def test_new_detail_review_blocks_old_existing_and_preserves_input():
    old = {'source': 'intervesp', 'source_url': 'url', 'classification': 'EXISTING_CONFIRMED',
           'sterbrust_product_id': '123', 'new_candidate_id': ''}
    saved = deepcopy(old)
    output = apply_detail_quarantine([old], {'url': {'status': 'REVIEW', 'reason': 'suffix contradiction', 'evidence_ref': 'verified'}})
    assert output[0]['classification'] == 'REVIEW'
    assert not output[0]['sterbrust_product_id']
    assert output[0]['detail_review_evidence'] == 'verified'
    assert old == saved


def test_exclusive_readiness_counts_not_double_counted():
    rows = [{'classification': 'EXISTING_CONFIRMED'}, {'classification': 'CONFLICT'},
            {'classification': 'NEW_CANDIDATE', 'readiness': {'state': 'READY_TO_CREATE_FULL'}},
            {'classification': 'NEW_CANDIDATE', 'readiness': {'identity_blockers': ['NEW_NEEDS_SECTION', 'NEW_NEEDS_IDENTITY_EVIDENCE']}}]
    counts = final_counts(rows)
    assert sum(counts.values()) == 4
    assert terminal_bucket(rows[-1]) == 'NEW_NEEDS_SECTION'
    assert terminal_bucket({'classification': 'NEW_CANDIDATE', 'readiness': {'state': 'REVIEW'}}) == 'REVIEW'


def test_explicit_equipment_scopes_do_not_infer_identity_from_brand():
    assert equipment_scope('Токарный станок TU2304V') == 'lathe'
    assert equipment_scope('Шлифовальный станок LX20 Pro') == 'grinder'
    assert equipment_scope('Станок TU2304V') != 'lathe'
    assert equipment_scope('Токарный станок', 'Шлифовальные станки') == 'equipment_scope_conflict'


def test_only_explicit_once_only_isolated_timeout_continuation_without_retry():
    stopped={'stage':'STOPPED_HTTP_ACCESS_OR_ERROR','rows':[
        {'url':'ok','status':'PERSISTED_ENRICHMENT','http_status':200},
        {'url':'failed','status':'NETWORK_ERROR','http_status':None,'diagnostics':['network_error:TimeoutError']}]}
    result=reviewed_timeout_continuation(stopped)
    assert result['rows'][-1]['status']=='REVIEW_HTTP_TIMEOUT'
    assert result['timeout_continuation']['failed_url_not_retried']=='failed'
    assert stopped['stage']=='STOPPED_HTTP_ACCESS_OR_ERROR'
    with pytest.raises(RuntimeError): reviewed_timeout_continuation(result)
    for change in ({'http_status':429}, {'status':'CAPTCHA'}, {'diagnostics':['network_error:ConnectionError']}):
        bad=deepcopy(stopped); bad['rows'][-1].update(change)
        with pytest.raises(RuntimeError): reviewed_timeout_continuation(bad)


def test_typed_group_anchor_requires_safe_exact_supplier_category_scope():
    row={'source':'intervesp','source_category':'Ленточнопильные станки',
         'name':'Ленточнопильный станок BMS230','properties':{'Длина пильной ленты, мм':'3660'}}
    mapping={'source':'intervesp','source_category':row['source_category'],'category_scope':'bandsaw',
             'verdict':'SAFE','supplier_property_name':'Длина пильной ленты, мм','canonical_property_key':'blade_length'}
    output=scoped_group_anchors(row,{'synonym_mappings':[mapping]})
    assert output['properties']['typed_bandsaw_blade_length']=='3660 mm'
    assert 'typed_bandsaw_blade_length' not in row['properties']
    for change in ({'verdict':'REVIEW'}, {'source':'beka_mak'}, {'source_category':'Other'}, {'category_scope':'lathe'}):
        result=scoped_group_anchors(row,{'synonym_mappings':[{**mapping,**change}]})
        assert not result['typed_group_anchor_evidence']['anchors']


def test_old_beka_review_not_auto_confirmed_by_replaying_same_source_data():
    row={'source':'beka_mak','external_id':'1','classification':'EXISTING_CONFIRMED','sterbrust_product_id':'123'}
    prior={('beka_mak','1'):{'classification':'REVIEW','match_status':'PRIOR_REVIEW_PENDING_OPERATOR',
                             'match_conflicts':['card_model_conflict']}}
    result=preserve_unresolved_reviews([row],prior)[0]
    assert result['classification']=='REVIEW' and not result['sterbrust_product_id']
    assert 'card_model_conflict' in result['match_conflicts']['prior_conflicts']
    assert row['classification']=='EXISTING_CONFIRMED'


def test_duplicate_raw_characteristics_quarantine_anchor_without_aborting_other_rows():
    name = 'Длина пильной ленты, мм'
    row = {'source':'intervesp','source_category':'Ленточнопильные станки',
           'name':'Ленточнопильный станок BMS230','properties':{name:'4000'},
           'observed_properties':[{'name':name,'value':'3660'},{'name':name,'value':'4000'}]}
    mapping = {'source':'intervesp','source_category':row['source_category'],'category_scope':'bandsaw',
               'verdict':'SAFE','supplier_property_name':name,'canonical_property_key':'blade_length'}
    result = scoped_group_anchors(row, {'synonym_mappings':[mapping]})
    assert result['semantic_conflicts']['typed_geometric_anchor'] == ['typed_bandsaw_blade_length']
    assert not result['typed_group_anchor_evidence']['anchors']
    assert len(result['observed_properties']) == 2
    assert 'semantic_conflicts' not in row


def test_final_matching_waits_for_identity_not_optional_price_content():
    from universal_supplier.intervesp_final_proposals import remaining_detail_buckets, assert_matching_identity_complete
    plan=[{'url':'identity','reason':'identity'},{'url':'price','reason':'price'},
          {'url':'chars','reason':'characteristics'},{'url':'both','reason':'price_and_characteristics'}]
    report={'stage':'IDENTITY_ENRICHMENT_COMPLETE','plan_count':4,
            'rows':[{'url':'identity','status':'REVIEW'}]}
    assert set(assert_matching_identity_complete(report,plan))=={'identity'}
    assert {k:len(v) for k,v in remaining_detail_buckets(plan,report).items()}=={
        'identity-critical':0,'price-only':1,'characteristics-only':1,'price+characteristics':1}
    with pytest.raises(ValueError): assert_matching_identity_complete({**report,'rows':[]},plan)
    with pytest.raises(ValueError): assert_matching_identity_complete({**report,'stage':'RUNNING'},plan)
    with pytest.raises(ValueError): assert_matching_identity_complete({**report,'rows':report['rows']*2},plan)
    with pytest.raises(ValueError): assert_matching_identity_complete({**report,'rows':[{'url':'identity','status':'NETWORK_ERROR'}]},plan)
