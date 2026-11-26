"""Candidate recall must not hide duplicate identity or weaken F01/F03."""
from dataclasses import replace
import json
import pytest
from tests.test_automatic_matching import card
from universal_supplier.matching_service import load_registry_from_postgres,_source,_candidates,_candidate,match_cards
from universal_supplier.automatic_matching import plan_card

class Connection:
    def __init__(self,rows):self.rows=rows;self.result=[]
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=()):
        assert sql.lstrip().startswith('SELECT')
        self.result=self.rows if 'FROM sterbrust_products' in sql else []
    def fetchall(self):return self.result

def target(key='101',name='Токарный станок OPTIturn TU 2304 V',model='',brand='Optimum',properties=None):
    return (key,name,brand,model,'','Токарные станки',properties or {})

def source(name='Токарный станок OPTIturn TU 2304 V'):
    return card(supplier_code='optimum',brand='Optimum',name=name,sku='')

def registry(*rows):return load_registry_from_postgres(Connection(rows))

def test_missing_catalog_model_is_recovered_from_own_visible_title():
    r=registry(target());result=plan_card(source(),r)
    assert result['bucket']=='AUTO' and result['decision']['sterbrust_product_id']=='101'
    assert r.products['101']['model']==''  # no mutation of the registry fact

def test_missing_model_duplicate_is_not_hidden_by_populated_model_index():
    r=registry(target(model='TU2304V'),target('102'))
    result=plan_card(source(),r)
    assert result['bucket']=='REVIEW' and result['reason']=='NON_UNIQUE_STRONG_CANDIDATES'
    assert json.loads(result['decision']['evidence'])['candidate_keys']==['101','102']

def test_visible_model_retrieval_does_not_replace_contradictory_raw_model():
    r=registry(target(name='Дисковый отрезной станок BEKA-MAK BMDO-100CS',
                      model='HSS BEKA-MAK BMDO-100CS',brand='Beka-Mak'))
    s=card(supplier_code='beka_mak',brand='Beka-Mak',name='Дисковый отрезной станок BEKA-MAK BMDO-100CS',sku='')
    assert [x['sterbrust_product_id'] for x in _candidates(_source(s),r)]==['101']
    assert _candidate(r.products['101']).model=='HSS BEKA-MAK BMDO-100CS'
    assert plan_card(s,r)['bucket']!='AUTO'

@pytest.mark.parametrize('other',['Токарный станок OPTIturn TU2304',
    'Токарный станок OPTIturn TU2304V PRO','Токарный станок OPTIturn TU2304V NEW',
    'Токарный станок OPTIturn TU2304V CNC','Токарный станок OPTIturn TU2304V+',
    'Патрон для токарного станка OPTIturn TU2304V'])
def test_visible_title_projection_keeps_execution_and_accessory_guards(other):
    assert plan_card(source(),registry(target(name=other)))['bucket']!='AUTO'

def test_missing_model_projection_still_obeys_property_conflict():
    r=registry(target(properties={'items':[{'property_name':'Напряжение','value_flat':'380 В'}]}))
    from universal_supplier.models import PropertyValue
    assert plan_card(replace(source(),properties=(PropertyValue('Напряжение','220 В'),)),r)['bucket']!='AUTO'

def test_title_cannot_replace_explicit_contradictory_model_property():
    r=registry(target(properties={'items':[{'property_name':'Модель','value_flat':'TU2304'}]}))
    assert plan_card(source(),r)['bucket']!='AUTO'

def test_existing_manual_product_id_is_not_replaced_by_discovered_title():
    r=registry(target(model='TU2304V'),target('102',name='Токарный станок OPTIturn TU2304V PRO'))
    result=plan_card(source(),r,{'sterbrust_product_id':'102','manual_or_human_confirmed':True})
    assert result['preserved_sterbrust_product_id']=='102' and result['write_action']=='PRESERVE_EXISTING'

@pytest.mark.parametrize('title',[
 'Электрогидравлический 4х-валковый станок Sahinler 4R HC 10-155',
 'Гидравлический 4х-валковый станок Sahinler серии 4R HSS',
 'Электрогидравлический 4х-валковый станок Sahinler 4RH'])
def test_roller_count_cannot_identify_distinct_sahinler_series(title):
    s=card(supplier_code='vekprom',brand='Sahinler',name=title,sku='')
    r=registry(target(name='Электрогидравлический 4х-валковый станок Sahinler 4R HC 10-185',brand='Sahinler'))
    assert plan_card(s,r)['bucket']!='AUTO'

def test_nonidentifying_model_does_not_hide_a_hard_property_conflict():
    from universal_supplier.models import PropertyValue
    name='Электрогидравлический 4х-валковый станок Sahinler 4RH'
    s=card(supplier_code='vekprom',brand='Sahinler',name=name,sku='',properties=(PropertyValue('Напряжение','220 В'),))
    r=registry(target(name=name,model='4х-валковый',brand='Sahinler',
        properties={'items':[{'property_name':'Напряжение','value_flat':'380 В'}]}))
    result=plan_card(s,r)
    assert result['bucket']=='CONFLICT'
