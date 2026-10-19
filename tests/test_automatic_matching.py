from collections import defaultdict
from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from universal_supplier.automatic_matching import plan_card, hydrate_card, runtime_matching_policy
from universal_supplier.matching_service import RegistryContext
from universal_supplier.models import ProductCard, PropertyValue
from universal_supplier.postgres import PostgresRepository


def card(**kw):
    values = dict(supplier_code='partner_st',external_id='source-1',sku='42',name='JET BD-7',
        requested_url='https://example.test/1',final_url='https://example.test/1',canonical_url='https://example.test/1',
        http_status=200,redirect_urls=(),price=None,old_price=None,currency='',availability_raw='',
        availability_normalized='unknown',quantity=None,description_text='',description_html='',brand='JET')
    return ProductCard(**(values | kw))


def registry(*rows):
    products = {str(i): {'sterbrust_product_id':str(i),'name':'JET BD-7','brand':'JET','model':'BD-7',
        'article':'','category':'','properties':{},'supplier_identifiers':{'partner_st':['42']}} | values
        for i,values in enumerate(rows or ({},),1)}
    return RegistryContext(products,{('partner_st','42'):list(products)},
        {'bd7':list(products)},{'jet':list(products)},defaultdict(list))


def test_exact_auto_deterministic_and_inputs_immutable():
    c=card();r=registry();before=repr((c,r))
    a=plan_card(c,r);b=plan_card(c,r)
    assert a==b and a['bucket']=='AUTO' and a['write_action']=='ACCEPT_EXACT'
    assert repr((c,r))==before


def test_duplicate_exact_article_is_review_never_auto():
    p=plan_card(card(),registry({},{}))
    assert p['bucket']=='REVIEW' and p['reason']=='DUPLICATE_SUPPLIER_ARTICLE'


@pytest.mark.parametrize('name',['JET BD-7 PRO','JET BD-7 380 В','Патрон для JET BD-7'])
def test_execution_voltage_and_accessory_do_not_false_auto(name):
    p=plan_card(card(name=name),registry({'name':'JET BD-7 220 В'}))
    assert p['bucket'] in {'CONFLICT','REVIEW'}
    assert p['decision']['auto_accepted']=='FALSE'


def test_retained_commercial_properties_enter_strict_guards():
    c=card(raw_data={'source_content':{'technical_properties':[['Напряжение','220 В'],['Напряжение','380 В']]}})
    assert len(hydrate_card(c).properties)==2 and c.properties==()
    assert plan_card(c,registry())['bucket'] in {'REVIEW','CONFLICT'}


def test_raw_pair_does_not_duplicate_same_normalized_observation_without_its_unit():
    c=card(properties=(PropertyValue('Диаметр, мм','300','мм'),),
        raw_data={'source_content':{'technical_properties':[['Диаметр, мм','300']]}})
    assert hydrate_card(c).properties==c.properties
    fresh=hydrate_card(replace(c,properties=()))
    assert fresh.properties[0].unit=='мм'


def test_ambiguous_observations_are_review_and_never_accepted():
    c=card(raw_data={'source_content':{'technical_properties':[['Напряжение','220 В'],['Напряжение','380 В']]}})
    p=plan_card(c,registry())
    assert p['bucket']=='REVIEW' and p['reason']=='AMBIGUOUS_CRITICAL_OBSERVATIONS'
    assert p['decision']['auto_accepted']=='FALSE'


def test_manufacturer_article_discovery_uses_explicit_proof_not_brand_top_ten():
    r=registry({'article':'M-123','supplier_identifiers':{}})
    r.identifier_index={};r.by_model={};r.by_brand={}
    from sterbrust_matching.normalization import article_lookup_key
    r.manufacturer_identifier_index={article_lookup_key('M-123'):['1']}
    c=card(sku='M-123',raw_data={'source_identity':{'manufacturer_article_explicit':True}})
    assert plan_card(c,r)['bucket']=='AUTO'
    assert plan_card(replace(c,raw_data={}),r)['bucket']!='AUTO'


def test_same_model_without_brand_is_review_not_new_catalogue_absence():
    r=registry({'brand':'UNOBSERVED','supplier_identifiers':{}})
    r.identifier_index={}
    r.by_model={'bd7':['1']}
    p=plan_card(card(brand='',name='BD-7',sku=''),r)
    assert p['bucket']=='REVIEW'


def test_contradictory_saved_brands_never_become_auto():
    c=card(raw_data={'source_identity':{'brand':'Optimum'}})
    p=plan_card(c,registry())
    assert p['bucket']=='REVIEW' and p['reason']=='AMBIGUOUS_SAVED_BRAND'


def test_literal_brand_can_corroborate_exact_model_but_supplier_name_cannot():
    r=registry({'supplier_identifiers':{}});r.identifier_index={}
    assert plan_card(card(brand='',sku=''),r)['bucket']=='AUTO'
    assert plan_card(card(brand='',sku='',name='BD-7'),r)['bucket']=='REVIEW'
    assert plan_card(card(brand='',sku='',name='Патрон для JET BD-7'),r)['bucket']!='AUTO'


@pytest.mark.parametrize('manual',[True,False])
def test_existing_id_is_directly_checked_without_fuzzy_or_history_change(manual):
    c=card(name='JET BD-7 PRO');existing={'sterbrust_product_id':'1','manual_or_human_confirmed':manual}
    with patch('universal_supplier.automatic_matching.match_cards',side_effect=AssertionError('fuzzy forbidden')):
        p=plan_card(c,registry(),existing)
    assert p['preserved_sterbrust_product_id']=='1' and p['write_action']=='PRESERVE_EXISTING'
    assert p['bucket']=='CONFLICT'
    assert existing=={'sterbrust_product_id':'1','manual_or_human_confirmed':manual}


def test_inactive_and_unstable_have_explicit_outcomes():
    assert plan_card(card(),registry(),active=False)['bucket']=='NOT_PROCESSED'
    assert plan_card(card(),registry(),stable_identity=False)['bucket']=='REVIEW'


def test_empty_registry_creates_candidate_only_and_missing_identity_is_review():
    r=RegistryContext({}, {}, {}, {}, {})
    assert plan_card(card(),r)['bucket']=='NEW'
    assert plan_card(card(name='Безымянное изделие',sku='',brand=''),r)['bucket']=='REVIEW'


def test_rc_approval_changes_execution_mode_not_semantic_guards(monkeypatch):
    cfg=SimpleNamespace(host='127.0.0.1',port=55461,dbname='sterbrust_unified_rc_20261019')
    monkeypatch.delenv('STERBRUST_RC_MATCHING_APPLY_APPROVED',raising=False)
    assert not runtime_matching_policy(cfg)['apply_allowed']
    monkeypatch.setenv('STERBRUST_RC_MATCHING_APPLY_APPROVED','YES')
    assert runtime_matching_policy(cfg)['apply_allowed']
    assert runtime_matching_policy(cfg)['f01_f03_enforced']
    assert plan_card(card(),registry({},{}))['bucket']=='REVIEW'


def test_locked_existing_mapping_short_circuits_all_mutations_even_stale_auto_plan():
    class Cursor:
        def __init__(self):self.queries=[]
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,params):self.queries.append(sql)
        def fetchall(self):return []
        def fetchone(self):return (1,999)
    q=Cursor();repo=object.__new__(PostgresRepository)
    repo.connection=SimpleNamespace(transaction=nullcontext,cursor=lambda:q)
    p=plan_card(card(),registry())
    result=repo.persist_match(p['decision'],'2026-10-19T00:00:00Z','partner_st',preserve_existing_links=True)
    assert result=='PRESERVED_EXISTING_MAPPING'
    assert len(q.queries)==2 and all('FOR UPDATE' in sql for sql in q.queries)
    assert all(sql.lstrip().startswith('SELECT') for sql in q.queries)


def test_panel_shows_offline_statistics_and_no_bypass(monkeypatch,tmp_path):
    from universal_supplier.control_plane.web import create_app
    from universal_supplier.control_plane.store import UnavailableControlPlaneStore
    from universal_supplier.automatic_matching import POLICY_VERSION
    p=tmp_path/'summary.json'
    p.write_text(json.dumps({'policy_version':POLICY_VERSION,'total':1,'dump_sha256':'fixture',
        'by_source':{'fixture':{'AUTO':1,'REVIEW':0,'CONFLICT':0,'NEW':0,'NOT_PROCESSED':0}}}),encoding='utf-8')
    monkeypatch.setenv('AUTOMATIC_MATCHING_REPORT',str(p));monkeypatch.setenv('STERBRUST_UNIFIED_RC','YES')
    client=TestClient(create_app(store=UnavailableControlPlaneStore('offline'),auth_required=False))
    response=client.get('/automatic-matching')
    assert response.status_code==200 and 'offline dry-run' in response.text and 'fixture' in response.text
    assert 'отдельного разрешения' in response.text and 'обхода защит' in response.text
    assert client.post('/automatic-matching').status_code==405


@pytest.mark.parametrize('apply',[False,True])
def test_shared_runtime_preview_and_authorized_apply_use_identical_plans(monkeypatch,apply):
    import universal_supplier.automatic_matching as service
    calls=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,params):self.sql=sql
        def fetchall(self):return [('old','1')] if 'sterbrust_product_id' in self.sql else []
    class Repo:
        connection=SimpleNamespace(cursor=Cursor,transaction=nullcontext)
        def __init__(self,*a):pass
        def load_existing_link_contexts(self,*a):return {}
        def persist_match(self,d,at,code,**kw):
            assert kw=={'preserve_existing_links':True}
            calls.append(d);return 'INSERTED_EVENT'
        def close(self):pass
    cfg=SimpleNamespace(host='127.0.0.1',port=55461,dbname='sterbrust_unified_rc_20261019')
    monkeypatch.setattr('universal_supplier.postgres.PostgresRepository',Repo)
    monkeypatch.setattr('universal_supplier.matching_service.load_registry_from_postgres',lambda c:registry())
    monkeypatch.setattr('universal_supplier.identity_service._load_cards',lambda c,s:
        ([card(external_id='old'),card(external_id='new'),card(external_id='not-in-batch')],{('partner_st','old'):1,('partner_st','new'):2}))
    monkeypatch.setattr(service,'_materialize_decision',lambda c,s,card:None)
    monkeypatch.setattr('universal_supplier.control_plane.admin_store.PostgresAdminStore',
        lambda c:SimpleNamespace(generate_review_cases=lambda s,source_ids: {'count':len(source_ids)}))
    monkeypatch.delenv('STERBRUST_RC_MATCHING_APPLY_APPROVED',raising=False)
    if apply:monkeypatch.setenv('STERBRUST_RC_MATCHING_APPLY_APPROVED','YES')
    result=service.run_post_ingest(cfg,'partner_st',['old','new'])
    assert result['total']==2 and result['by_source']['partner_st']['AUTO']==2
    assert result['matching_applied'] is apply
    assert len(calls)==int(apply)
    if apply:assert calls[0]['source_external_id']=='new'
