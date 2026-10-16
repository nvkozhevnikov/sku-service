from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import extract_model, model_identity_evidence, normalize_model
from universal_supplier import identity_service as identity
from universal_supplier.matching_input import matching_product, with_persisted_evidence
from universal_supplier.matching_service import RegistryContext, _candidates, _source
from universal_supplier.models import ProductCard
from scripts import run_ltt_uss_matching as entry


def card(**updates):
    data = dict(supplier_code='ltt_msk', external_id='42', sku='', name='Станок XZ20',
        requested_url='https://fixture.invalid/42', final_url='https://fixture.invalid/42',
        canonical_url='https://fixture.invalid/42', http_status=200, redirect_urls=(),
        price=None, old_price=None, currency='', availability_raw='',
        availability_normalized='unknown', quantity=None, description_text='', description_html='')
    data.update(updates)
    return ProductCard(**data)


@pytest.mark.parametrize('base', ['45/500S', '50/650S', 'AB12'])
def test_literal_plus_is_execution_in_normalization_and_match(base):
    assert normalize_model(base+'+') != normalize_model(base)
    assert extract_model('Станок '+base+'+') == normalize_model(base+'+')
    assert model_identity_evidence('Станок '+base+'+')['execution_qualifier'] == '+'
    for left, right in [(base+'+',base),(base,base+'+')]:
        source=Product('s','Станок '+left,brand='FixtureBrand',model=left,product_kind='machine')
        target=Product('t','Станок '+right,brand='FixtureBrand',model=right,product_kind='machine')
        result=match_product(source,[target])
        assert not result.auto_accepted
        assert 'model_execution' in result.conflicts


def test_plus_in_compound_model_and_bundle_prose():
    assert extract_model('Пила AB12+CD34') == 'ab12+cd34'
    assert extract_model('Станок LTT-Z1410 (100Вт) + чиллер 3000') == 'lttz1410'
    assert normalize_model(' AB 12 ＋ ') == 'ab12+'
    assert normalize_model('AB12++') != normalize_model('AB12+')
    assert match_product(Product('s','Станок AB12+',brand='Fixture',model='AB12+'),
                         [Product('t','Станок AB12+',brand='Fixture',model='AB12+')]).auto_accepted


@pytest.mark.parametrize('suffix',['PRO','NEW','V','NC','CNC'])
def test_other_execution_tokens_remain_significant(suffix):
    assert normalize_model('AB12 '+suffix) != normalize_model('AB12')
    result=match_product(Product('s','Станок AB12 '+suffix,brand='Fixture',model='AB12 '+suffix),
                         [Product('t','Станок AB12',brand='Fixture',model='AB12')])
    assert not result.auto_accepted


@pytest.mark.parametrize('kind',['machine','accessory','spare','disk','tool','tooling','knife','collet','handle','fixture','power_feeder'])
@pytest.mark.parametrize('namespace',entry.SOURCES)
def test_persisted_kind_brand_model_category_and_namespace(kind,namespace):
    c=card(supplier_code=namespace,name='Изделие без модели',raw_data={
        'source_identity':{'raw_model':'XZ20 PRO','site_internal_id':'42'},
        'enrichment_evidence':{'source_brand':'StoredBrand','model_raw':'XZ20 PRO',
            'source_category':'Stored category','product_kind':{'product_kind':kind,'confidence':'HIGH'}}})
    before=deepcopy(c.as_jsonable())
    enriched=with_persisted_evidence(c)
    source=_source(enriched)
    assert source == identity.source_product(enriched.as_jsonable())
    assert (source.key,source.supplier_code,source.brand,source.model,source.product_kind,source.category)==(
        '42',namespace,'StoredBrand','xz20pro',kind,'Stored category')
    assert c.as_jsonable()==before


def test_absent_evidence_does_not_invent_brand_kind_category():
    product=matching_product(card(name='Безымянное изделие').as_jsonable())
    assert product.brand==product.model==product.product_kind==product.category==''


@pytest.mark.parametrize('kind',['accessory','spare','disk','tool','knife','collet','handle','fixture','power_feeder'])
def test_stored_non_machine_kind_prevents_brand_model_machine_autolink(kind):
    c=card(raw_data={'source_identity':{'raw_model':'XZ20'},'enrichment_evidence':{
        'source_brand':'Fixture','product_kind':{'product_kind':kind}}})
    result=match_product(_source(c),[Product('canon','Станок XZ20',brand='Fixture',model='XZ20',product_kind='machine')])
    assert not result.auto_accepted
    assert 'product_kind' in result.conflicts


@pytest.mark.parametrize('left,right',[
    ('Станок LX20 Pro','Станок LX20 NEW'),('Станок PP-13D NEW','Станок PP-13D'),
    ('Токарный станок TU2304V','Токарный станок TU2304'),
    ('Контроллер для манипулятора ETM-16U','Резьбонарезной манипулятор ETM-16U'),
    ('Ручка для манипулятора ETM-16U','Резьбонарезной манипулятор ETM-16U'),
    ('Губки для тисков HCV125','Гидравлические тиски HCV125'),
])
def test_execution_and_sold_object_protected_negatives(left,right):
    source=_source(card(name=left,brand='Fixture'))
    target=Product('canon',right,brand='Fixture',model=extract_model(right))
    assert not match_product(source,[target]).auto_accepted


def test_missing_kind_or_characteristics_alone_does_not_block_identity():
    source=_source(card(name='Токарный станок XZ20',brand='Fixture'))
    target=Product('canon','Токарный станок XZ20',brand='Fixture',model='XZ20')
    assert not source.properties and not source.product_kind
    assert match_product(source,[target]).auto_accepted


def test_identical_supplier_article_stays_namespaced():
    products={str(i):{'sterbrust_product_id':str(i)} for i in range(4)}
    registry=RegistryContext(products,{(code,'42'):[str(i)] for i,code in enumerate(entry.SOURCES)}, {},{}, {})
    for i,code in enumerate(entry.SOURCES):
        assert _candidates(Product('s','',supplier_code=code,supplier_article='42'),registry)==[products[str(i)]]


def service_fakes(monkeypatch, *, empty=False, missing_catalog=0):
    class Connection:
        def execute(self,sql,*args):
            assert sql.startswith('SELECT count(*) FROM sterbrust_products')
            return SimpleNamespace(fetchone=lambda:(missing_catalog,))
        def cursor(self):return self
        def transaction(self):return nullcontext()
        def __enter__(self):return self
        def __exit__(self,*args):pass
    connection=Connection()
    inserts=[]
    def audit_execute(sql,*args):
        assert sql.startswith('INSERT INTO product_identity_decisions')
        inserts.append(sql)
    audit_connection=Mock()
    audit_connection.__enter__=Mock(return_value=audit_connection)
    audit_connection.__exit__=Mock()
    audit_connection.transaction.return_value=nullcontext()
    cursor=Mock();cursor.__enter__=Mock(return_value=cursor);cursor.__exit__=Mock();cursor.execute.side_effect=audit_execute
    audit_connection.cursor.return_value=cursor
    import psycopg
    monkeypatch.setattr(psycopg,'connect',lambda **kw:audit_connection)
    repo=SimpleNamespace(connection=connection,close=Mock(),persist_match=Mock(return_value='INSERTED_EVENT'),load_existing_link_contexts=Mock(return_value={}))
    monkeypatch.setattr(identity,'PostgresRepository',lambda cfg:repo)
    regrow=dict(sterbrust_product_id='1',name='Станок XZ20',brand='Fixture',model='XZ20',article='',category='',properties={},supplier_identifiers={})
    registry=RegistryContext({} if empty else {'1':regrow},{},{'xz20':['1']},{'fixture':['1']},{})
    monkeypatch.setattr(identity,'load_registry_from_postgres',lambda c:registry)
    monkeypatch.setattr(identity,'_load_cards',lambda c,s:([card(brand='Fixture')],{('ltt_msk','42'):1}))
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    monkeypatch.setattr(PostgresAdminStore,'generate_review_cases',lambda self,s:{'inserted':1})
    from universal_supplier.offer_selection import PostgresOfferSelectionStore
    monkeypatch.setattr(PostgresOfferSelectionStore,'__init__',lambda *args:pytest.fail('selection constructed'))
    return repo,inserts


def test_matching_only_materializes_using_existing_services_without_selection(monkeypatch):
    repo,inserts=service_fakes(monkeypatch)
    result=identity.reconcile_matching_only(SimpleNamespace(kwargs=lambda:{}),'ltt_msk')
    assert result['source_products']==1 and result['offer_selection']=={'executed':False}
    assert repo.persist_match.call_count==1 and len(inserts)==1 and repo.close.called


@pytest.mark.parametrize('empty,missing,error',[(True,0,'REGISTRY_EMPTY'),(False,1,'BOOTSTRAP_INCOMPLETE')])
def test_matching_only_fails_before_decisions_if_registry_not_ready(monkeypatch,empty,missing,error):
    repo,inserts=service_fakes(monkeypatch,empty=empty,missing_catalog=missing)
    with pytest.raises(RuntimeError,match=error):
        identity.reconcile_matching_only(SimpleNamespace(kwargs=lambda:{}),'ltt_msk')
    repo.persist_match.assert_not_called();assert not inserts and repo.close.called


def test_apply_requires_flag_before_any_connection(monkeypatch):
    import psycopg
    monkeypatch.setattr(psycopg,'connect',lambda **kw:pytest.fail('connected'))
    with pytest.raises(ValueError,match='requires'):
        entry.execute(None,apply=True)
    with pytest.raises(SystemExit):entry.main(['--apply'])


def test_plan_is_read_only_and_does_not_match(monkeypatch):
    import psycopg
    connection=Mock();connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock()
    connection.execute.return_value.fetchone.return_value=(0,0)
    def connect(**kw):
        assert kw['options']=='-c default_transaction_read_only=on'
        return connection
    monkeypatch.setattr(psycopg,'connect',connect)
    monkeypatch.setattr(entry,'_load_cards',lambda c,s:([card(supplier_code=s)],{}))
    monkeypatch.setattr(entry,'reconcile_matching_only',lambda *args:pytest.fail('matching executed'))
    cfg=SimpleNamespace(host='127.0.0.1',port=55455,dbname='universal_supplier_ltt_uss',kwargs=lambda:{})
    result=entry.execute(cfg)
    assert result['db_writes']==0 and not result['matching_executed'] and not result['registry_ready']
    with pytest.raises(RuntimeError,match='NOT_READY'):entry.execute(cfg,apply=True,enable_matching_writes=True)


@pytest.mark.parametrize('model_left,model_right,kind_left,kind_right,grouped',[
    ('XZ20','XZ20','machine','machine',True),
    ('XZ20+','XZ20','machine','machine',False),
    ('XZ20 PRO','XZ20 NEW','machine','machine',False),
    ('XZ20','XZ20','fixture','machine',False),
])
def test_ltt_uss_grouping_requires_full_identity_and_independent_anchor(model_left,model_right,kind_left,kind_right,grouped):
    from universal_supplier.new_group_proposals import propose_new_groups
    def row(code,model,kind):
        return dict(source=code,external_id='42',new_candidate_id=code+':42',classification='NEW_CANDIDATE',
            brand='Fixture',model=model,execution=model,name='Станок '+model,product_kind=kind,
            model_role='OWN_MODEL',source_url='https://fixture.invalid/'+code,evidence_ref='fixture://'+code,
            properties={'diameter':'230 mm'})
    rows=[row('ltt_msk',model_left,kind_left),row('uss_stanko',model_right,kind_right)]
    result=propose_new_groups(rows,allowed_sources=set(entry.SOURCES))
    assert bool(result['groups']) is grouped
    for item in rows:item['properties']={}
    assert not propose_new_groups(rows,allowed_sources=set(entry.SOURCES))['groups']


def test_all_four_group_namespaces_are_supported_with_stable_separate_source_keys():
    from universal_supplier.new_group_proposals import propose_new_groups
    rows=[dict(source=code,external_id='42',new_candidate_id=code+':42',classification='NEW_CANDIDATE',
        brand='Fixture',model='XZ20',execution='XZ20',name='Станок XZ20',product_kind='machine',
        model_role='OWN_MODEL',source_url='https://fixture.invalid/'+code,evidence_ref='fixture://'+code,
        properties={'diameter':'230 mm'}) for code in entry.SOURCES]
    result=propose_new_groups(rows,allowed_sources=set(entry.SOURCES))
    assert len(result['groups'])==1 and len(result['groups'][0]['source_records'])==4
    assert not propose_new_groups(rows)['groups']  # Historical default remains narrow.
    with pytest.raises(ValueError):propose_new_groups(rows,allowed_sources={'production'})


def test_get_refresh_explicitly_selects_all_defined_supplier_article_properties():
    from scripts.snapshot_sterbrust import product_select_fields, ALLOWED_METHODS
    assert ALLOWED_METHODS=={'catalog.product.list','catalog.section.list','catalog.productProperty.list'}
    fields=product_select_fields([{'id':1546,'code':'ARTIKUL_PARTNER'},
                                 {'id':'9999','code':'ARTIKUL_FUTURE'}, {'id':22,'code':'OTHER'}])
    assert 'property1546' in fields and 'property9999' in fields and 'property22' not in fields
    assert len(fields)==len(set(fields))
