"""Frozen audited sold-object regressions; never connects to DB or suppliers."""
import json
from pathlib import Path
from dataclasses import asdict
import pytest
from sterbrust_matching.product_identity import classify_product_kind, TAXON_BY_KIND
from universal_supplier.adapters.ltt_uss_html import source_evidence

FIXTURE=Path(__file__).parent/'fixtures/product_kind_batch/decisions.json'
ROWS=json.loads(FIXTURE.read_text(encoding='utf8'))['rows']

@pytest.mark.parametrize('row',ROWS,ids=lambda a:f"{a['supplier']}_{a['index']}_{a['decision']}")
def test_each_audited_kind_decision(row):
    new=source_evidence(row['title'],row['category'],(),'',own_description=row['own_description'])['product_kind']
    if row['decision'] in {'VALID_MACHINE','OPERATOR_REVIEW_REQUIRED'}:
        assert new==row['old_kind']
    else:
        assert new['product_kind']==row['expected_kind']
        assert new['confidence']=='HIGH'
        assert new!=row['old_kind']

@pytest.mark.parametrize('title,description,expected,family',[
    ('Пневмобалка на форматный станок','Устанавливается на каретке.','fixture','tooling'),
    ('Гайка для патрона','Подходит к патрону сверлильного станка.','chuck_nut','component'),
    ('Сборные токарные резцы','','cutting_tool','tooling'),
    ('Вакуумный насос','Создает вакуум.','vacuum_pump','equipment'),
    ('Чиллер','Охлаждает лазерный излучатель.','chiller','equipment'),
    ('Торцевой подрезатель кромки','Позволяет перекусить кромку. Выполнен из пластика.','hand_edge_trimmer','tooling'),
])
def test_generic_sold_object_not_host_machine(title,description,expected,family):
    value=classify_product_kind(title,'Фрезерные станки',own_description=description)
    assert value.product_kind==expected
    assert TAXON_BY_KIND[value.product_kind].family==family

@pytest.mark.parametrize('title',[
    'Станок лазерный с чиллером',
    'Станок заточной для токарных резцов',
    'Форматный станок с пневмобалкой на каретке',
])
def test_complete_machine_head_precedes_tools(title):
    value=classify_product_kind(title,own_description='Устанавливается на другой станок.')
    assert TAXON_BY_KIND[value.product_kind].family=='machine'

def test_trimmer_requires_positive_own_description_not_model_price_or_weight():
    prior=asdict(classify_product_kind('Торцевой подрезатель кромки','Кромкооблицовочные станки'))
    unchanged=asdict(classify_product_kind('Торцевой подрезатель кромки','Кромкооблицовочные станки',
        own_description='Вес 0.6 кг. Сменные ножи. Цена 1000 руб.'))
    assert prior==unchanged and prior['product_kind']=='machine'

def test_audited_cohort_is_complete():
    assert len(ROWS)==397
    assert len({(a['supplier'],a['index'],a['terminal']) for a in ROWS})==397
    assert sum(a['decision']=='VALID_MACHINE' for a in ROWS)==362
    assert sum(a['decision']=='OPERATOR_REVIEW_REQUIRED' for a in ROWS)==2
    assert sum(a['terminal'] for a in ROWS)==5
