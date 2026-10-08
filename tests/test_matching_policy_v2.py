import pytest
import json
from pathlib import Path
from universal_supplier.matching_policy_v2 import PolicyV2Index, assess_pair, characteristic_evidence, title_represents, normalize_identity_model, plus_evidence


def row(model='TU2304V',name=None,brand='Optimum',**kw):
    return dict(model=model,name=name or 'Токарный станок '+model,brand=brand,
                source_category='Токарные станки',evidence_ref='frozen-sha',
                sterbrust_product_id='123',active='Y',observed_properties=[],properties={},**kw)


def test_strong_identity_without_characteristics():
    assert assess_pair(row(),row())['confirmed']
    assert assess_pair(row(),row())['evidence']['characteristics']['positive_characteristic_quota']==0


@pytest.mark.parametrize('left,right,leftname,rightname',[
    ('TU2304V','TU2304',None,None),('LX20 Pro','LX20 NEW',None,None),
    ('PP-13D NEW','PP-13D',None,None),
    ('ETM-16U','ETM-16U','Контроллер ETM-16U','Станок ETM-16U'),
    ('ETM-16U','ETM-16U','Ручка ETM-16U','Станок ETM-16U'),
    ('HCV125','HCV125','Губки HCV125','Тиски HCV125'),
    ('TU2304','TU2305',None,None),('AHS20/35','AHS20/50',None,None),
    ('200','200','Компрессор 200','Станок 200'),
])
def test_negative_guards(left,right,leftname,rightname):
    assert not assess_pair(row(left,leftname),row(right,rightname))['confirmed']


def test_brand_alias_scoped():
    a=row('SR-RP630','Шлифовальный станок EcoWood SR-RP630','EcoWood')
    b=row('SR-RP630','Шлифовальный станок HCM SR-RP630','HCM')
    assert assess_pair(a,b)['confirmed']
    a['model']=b['model']='SR-RP950'
    assert not assess_pair(a,b)['confirmed']
    assert not assess_pair(row(brand='Unapproved'),row())['confirmed']


def test_series_exact_variant_own_model_only():
    a=row('Smart-42T8',brand='JINN FA')
    b=row('Smart-42T8','Токарный станок SMART — Smart-42T8','JINN FA')
    assert assess_pair(a,b)['confirmed']
    b['model']='Smart-32'
    assert not assess_pair(a,b)['confirmed']
    a['card_kind']='SERIES_OR_GROUP_CARD'
    assert not assess_pair(a,row('Smart-42T8',brand='JINN FA'))['confirmed']


def test_duplicate_active_canonical_ids_fail_closed():
    index=PolicyV2Index({'123':row(),'124':{**row(),'sterbrust_product_id':'124'}})
    assert index.evaluate(row())['blocking_reasons']==['CANONICAL_IDENTITY_NOT_UNIQUE']


def test_missing_properties_not_conflict_but_same_role_bore_conflict():
    a=row();b=row()
    a['observed_properties']=[dict(name='Диаметр отверстия шпинделя, мм',value='40',unit='мм')]
    assert assess_pair(a,b)['confirmed']
    b['observed_properties']=[dict(name='Диаметр отверстия шпинделя, мм',value='50',unit='мм')]
    assert 'SAME_ROLE_TYPED_CONTRADICTION' in assess_pair(a,b)['blocking_reasons']


def test_title_execution_boundary():
    assert title_represents('Станок S65 TCM','S65 TCM')
    assert not title_represents('Станок TU2304V','TU2304')
    assert not title_represents('Станок LX20 Pro','LX20')
    assert not title_represents('Станок AHS20/350','AHS20/35')


def test_role_values_are_not_collapsed():
    a=row();b=row()
    a['observed_properties']=[dict(name='Диаметр отверстия шпинделя, мм',value='40',unit='мм',role='Шпиндель'),dict(name='Диаметр отверстия шпинделя, мм',value='20',unit='мм',role='Противошпиндель')]
    b['observed_properties']=[dict(name='Диаметр отверстия шпинделя, мм',value='40',unit='мм')]
    proof=characteristic_evidence(a,b)
    assert len(proof['agreements'])==1 and not proof['contradictions']


def test_all_35_operator_calibrations_from_hash_pinned_data():
    root=Path(__file__).resolve().parents[1]
    frozen=json.loads((root/'tests/fixtures/kami_v21_operator_calibrations.json').read_bytes())
    index=PolicyV2Index(frozen['targets'])
    assert len(frozen['cases'])==35
    assert frozen['input_hashes']['CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl']=='40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65'
    for fixture in frozen['cases']:
        audit=index.evaluate(fixture['source'])
        assert audit['retrieval_ids']==fixture['retrieval_ids']
        assert audit['confirmed'], (fixture['external_id'],audit['blocking_reasons'])
        assert audit['confirmed']['sterbrust_product_id']==fixture['expected_id']


def test_net_gross_and_power_roles_not_scalar_equivalent():
    a=row();b=row()
    a['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value='7.5',unit='кВт',role='S6')]
    b['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value='15',unit='кВт',role='Continuous')]
    assert not characteristic_evidence(a,b)['contradictions']


def test_same_role_power_unit_conversion_and_contradiction():
    a=row('CUT-250V','Ленточнопильный станок CUT-250V');b=row('CUT-250V','Ленточнопильный станок CUT-250V')
    a['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value='1,5',unit='кВт')]
    b['observed_properties']=[dict(name='Мощность главного двигателя, Вт',value='1500',unit='Вт')]
    assert characteristic_evidence(a,b)['agreements']
    b['observed_properties'][0]['value']='15000'
    assert characteristic_evidence(a,b)['contradictions']


def test_plus_execution_not_approved_as_base_model():
    # QA-discovered actual defect: variant:10105019 -> canonical145214.
    # Preserve the actual QA counterexample; never convert it to a positive.
    a=row('50/650S+','Токарный центр с наклонной станиной KMT 50/650S+','KMT')
    b=row('50/650S','Токарные центры с наклонной станиной KMT 50/650S','KMT')
    assert not assess_pair(a,b)['confirmed']


def test_same_role_main_power_contradiction_not_silently_unmapped():
    a=row('CUT-250V','Ленточнопильный станок CUT-250V');b=row('CUT-250V','Ленточнопильный станок CUT-250V')
    a['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value='1,5',unit='кВт')]
    b['observed_properties']=[dict(name='Мощность главного двигателя, Вт',value='15000',unit='Вт')]
    assert 'SAME_ROLE_TYPED_CONTRADICTION' in assess_pair(a,b)['blocking_reasons']


@pytest.mark.parametrize('a,b',[('45/500S+','45/500S'),('50/650S+','50/650S'),
    ('X+','X'),('X+Y','XY'),('X+Y','X'),('ATC+C','ATCC'),('6K+2','6K2'),
    ('45S+PRO','45SPRO'),('PRO + CTS30','PROCTS30')])
def test_significant_plus_normalization(a,b):
    assert normalize_identity_model(a)!=normalize_identity_model(b)


@pytest.mark.parametrize('model',['45/500S+','50/650S+','M200 ATC+C','6K+2','45S+PRO','PRO + CTS30'])
def test_same_plus_execution_positive(model):
    a=row(model,'Токарный станок '+model)
    assert assess_pair(a,a)['confirmed']
    assert title_represents(a['name'],model)


def test_descriptive_plus_not_execution():
    assert plus_evidence('machine + options')[0]['kind']=='PUNCTUATION_PLUS'
    assert plus_evidence('PRO + CTS30')[0]['kind']=='EXECUTION_PLUS'
    assert not title_represents('Станок 50/650S+','50/650S')


@pytest.mark.parametrize('kw,w', [('5.5','55000'),('11','1100'),('7.5','7.5')])
def test_material_same_role_power_examples(kw,w):
    a=row();b=row()
    a['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value=kw,unit='кВт')]
    b['observed_properties']=[dict(name='Мощность главного двигателя, Вт',value=w,unit='Вт')]
    result=assess_pair(a,b)
    assert not result['confirmed']
    assert result['evidence']['characteristics']['contradictions']


def test_spindle_power_not_installed_or_unproven_motor_power():
    a=row();b=row()
    a['observed_properties']=[dict(name='Мощность шпинделя, кВт',value='7.5',unit='кВт')]
    for label in ('Установленная мощность, кВт','Мощность двигателя, кВт'):
        b['observed_properties']=[dict(name=label,value='20',unit='кВт')]
        assert not characteristic_evidence(a,b)['contradictions']


def test_material_processing_capacity_not_spindle_bore():
    a=row();b=row()
    a['observed_properties']=[dict(name='Макс. диаметр точения, мм',value='200',unit='мм')]
    b['observed_properties']=[dict(name='Макс. диаметр точения, мм',value='300',unit='мм')]
    assert not assess_pair(a,b)['confirmed']
    b['observed_properties']=[dict(name='Диаметр отверстия шпинделя, мм',value='50',unit='мм')]
    assert assess_pair(a,b)['confirmed']


def test_capacity_position_roles_not_cross_compared():
    a=row();b=row()
    props=[dict(name='Макс. диаметр обработки над станиной, мм',value='500',unit='мм'),
           dict(name='Макс. диаметр обработки над суппортом, мм',value='310',unit='мм')]
    a['observed_properties']=props;b['observed_properties']=list(reversed(props))
    assert assess_pair(a,b)['confirmed']
    b['observed_properties'][0]={**b['observed_properties'][0],'value':'400'}
    assert not assess_pair(a,b)['confirmed']


def test_feed_polish_and_scoring_motors_not_cross_compared():
    a=row();b=row()
    a['observed_properties']=[dict(name='Мощность двигателя подачи, кВт',value='1.5',unit='кВт'),
        dict(name='Мощность двигателей полировки, кВт',value='0.18',unit='кВт')]
    b['observed_properties']=list(reversed(a['observed_properties']))
    assert assess_pair(a,b)['confirmed']
    b['observed_properties']=[dict(name='Мощность двигателя подрезной пилы',value='0.75 кВт')]
    assert assess_pair(a,b)['confirmed']


def test_ambiguous_multi_motor_fields_retained_not_convenient_scalar():
    a=row();b=row()
    a['observed_properties']=b['observed_properties']=[dict(name='Мощность двигателя, кВт',value='5.5',unit='кВт'),dict(name='Мощность двигателя, кВт',value='1.1',unit='кВт')]
    proof=characteristic_evidence(a,b)
    assert not proof['contradictions']
    assert proof['uncertain'][0]['reason']=='multiple_generic_motor_functions_not_aligned'


def test_bandsaw_main_drive_power_not_lost_between_feature_keys():
    a=row('KSY1100x1600','Ленточнопильный станок KSY1100x1600');b=row('KSY1100x1600','Ленточнопильный станок KSY1100x1600')
    a['observed_properties']=[dict(name='Главный привод, кВт',value='11',unit='кВт')]
    b['observed_properties']=[dict(name='Мощность, Вт',value='1100',unit='Вт'),dict(name='Мощность',value='11 кВт')]
    assert not assess_pair(a,b)['confirmed']
    assert characteristic_evidence(a,b)['contradictions']


def test_hydraulic_drive_not_main_power():
    a=row('CUT-250V','Ленточнопильный станок CUT-250V');b=row('CUT-250V','Ленточнопильный станок CUT-250V')
    a['observed_properties']=[dict(name='Главный привод, кВт',value='11',unit='кВт')]
    b['observed_properties']=[dict(name='Мощность гидравлического привода',value='2.2 кВт')]
    assert not characteristic_evidence(a,b)['contradictions']
