from copy import deepcopy
from universal_supplier.offline_canonical_closure import own_model_columns, strict_saved_resolution, evidence_route, source_support_inventory


def record(body,model='BMSY-320DGH'):
    return {'name':'Ленточнопильный станок '+model,'brand_raw':'Beka-Mak','model_raw':model,
            'sterbrust_product_id':'1','category_path':'Ленточнопильные станки','raw_data':{'detailText':body}}


def test_multi_model_description_uses_exact_execution_only():
    r=record('<table><tr><th>Параметр</th><th>BMSY-320DG</th><th>BMSY-320DGH</th></tr>'
             '<tr><td>Мощность двигателя, кВт</td><td>1.5</td><td>2.2</td></tr></table>')
    props=own_model_columns(r)
    assert props and props[0]['value']=='2.2' and props[0]['column']==2


def test_duplicate_headers_and_nested_related_links_are_not_evidence():
    assert own_model_columns(record('<table><tr><th>Параметр</th><th>BMSY-320DGH</th><th>BMSY-320DGH</th></tr></table>'))==[]
    assert own_model_columns(record('<table><tr><th>Параметр</th><th>BMSY-320DG</th><th>BMSY-320DGH</th></tr><tr><td><a>Мощность двигателя, кВт</a></td><td>1</td><td>2</td></tr></table>'))==[]


def test_metadata_alias_never_replaces_full_execution():
    assert own_model_columns(record('<table><tr><th>Параметр</th><th>BMSY-320DG</th><th>BMSY-320DG</th></tr></table>'))==[]


def test_empty_exact_retrieval_is_not_new_proof_or_mutation():
    row={'source':'optimum','model':'X1','classification':'REVIEW'};before=deepcopy(row)
    assert strict_saved_resolution(row,[])['confirmed'] is None and row==before
    assert evidence_route(row,strict_saved_resolution(row,[]),{})!='NEW_READINESS_CANDIDATE'


def test_article_and_quantity_are_not_two_typed_identity_anchors():
    inventory=source_support_inventory({'name':'Ленточнопильный станок X1','source_category':'Ленточнопильные станки',
                                        'observed_properties':[{'name':'Артикул','value':'123'},{'name':'Количество','value':'1'}]})
    assert not inventory['independent_source_anchors_present']
