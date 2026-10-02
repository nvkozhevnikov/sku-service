from universal_supplier.intervesp_selective_enrichment import post_matching_detail_plan


def row(url, classification='EXISTING_CONFIRMED', **values):
    return {'source':'intervesp','external_id':url,'source_url':url,
            'classification':classification,'price_state':'missing', **values}


def test_only_actual_existing_price_gap_not_request_or_unknown_stock():
    plan=[{'url':x,'reason':'price'} for x in ('missing','request','numeric')]
    report={'rows':[]}
    result=post_matching_detail_plan([row('missing'),row('request',price_state='price_on_request'),
                                      row('numeric',price_state='numeric_public',availability='unknown')],plan,report)
    assert [x['url'] for x in result['selected_urls']]==['missing']
    assert result['deferred_count']==2


def test_one_full_content_request_per_new_group_and_never_repeat_saved():
    ready={'identity_ready':True,'full_ready':False}
    rows=[row('a','NEW_CANDIDATE',new_group_id='g',readiness=ready),
          row('b','NEW_CANDIDATE',new_group_id='g',readiness=ready)]
    result=post_matching_detail_plan(rows,[],{'rows':[]})
    assert len(result['new_full_content_outside_original_plan'])==1
    assert result['minimum_proposed_gets']==1
    result=post_matching_detail_plan(rows,[{'url':'a','reason':'characteristics'}],{'rows':[{'url':'a'}]})
    assert all(x['url']!='a' for x in result['new_full_content_outside_original_plan'])


def test_review_requires_own_full_model_and_no_contradiction():
    candidate={'full_model_confirmed':True,'proposed_sterbrust_id':'123','model_role':'OWN_MODEL',
               'brand':'Beka-Mak','product_kind':'bandsaw','match_method':'BRAND_MODEL_IDENTITY_REVIEW'}
    plan=[{'url':x,'reason':'characteristics'} for x in ('okay','suffix','accessory','unknown')]
    rows=[row('okay','REVIEW',**candidate),row('suffix','REVIEW',**candidate,match_conflicts=['WP != base']),
          row('accessory','REVIEW',**{**candidate,'product_kind':'jaw'}),
          row('unknown','REVIEW',**{**candidate,'full_model_confirmed':False})]
    result=post_matching_detail_plan(rows,plan,{'rows':[]})
    assert [x['url'] for x in result['selected_urls']]==['okay']
