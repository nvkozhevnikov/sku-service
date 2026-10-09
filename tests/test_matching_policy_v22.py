from copy import deepcopy
import pytest
from tests.test_matching_policy_v2 import row
from universal_supplier.matching_policy_v2 import assess_pair
from universal_supplier.matching_policy_v22 import assess_pair_v22,kind_proof,PolicyV22Index

def pair(name,model='V11 10000',brand='BLIN'):
    return row(model,name,brand)

def test_complete_machining_center_not_rotating_tool_center():
    s=pair('Вертикальный фрезерный обрабатывающий центр BLIN V11 10000')
    t=deepcopy(s)
    assert not assess_pair(s,t)['confirmed']
    proof=assess_pair_v22(s,t)
    assert proof['confirmed'] and proof['new_rule_applied']
    assert proof['evidence']['kind_evidence'][0]['kind']=='milling_machine'
    assert proof['evidence']['negative_guard_result']['all_current_guards_pass']

def test_generic_belt_machine_refined_by_explicit_bandsaw_category():
    s=pair('Автоматический ленточнопильный станок Beka-Mak BMSO 230','BMSO230','Beka-Mak')
    t=pair('Автоматический ленточный станок Beka-Mak BMSO 230','BMSO230','Beka-Mak')
    t['source_category']='Ленточнопильные станки / Автоматические'
    assert not assess_pair(s,t)['confirmed']
    assert assess_pair_v22(s,t)['confirmed']

def test_model_only_visible_title_with_explicit_manufacturer_category():
    s=pair('BMSY-613VT','BMSY-613VT','Beka-Mak');s['source_category']='Band saw'
    t=pair('BMSY-613VT Вертикальный ленточнопильный станок','BMSY-613VT','Beka-Mak')
    assert assess_pair_v22(s,t)['confirmed']
    assert not assess_pair_v22(s,t,allow_category=False)['confirmed']
    s['source_category']='Hydraulıc Arm'
    assert not assess_pair_v22(s,t)['confirmed']

def test_hydraulic_press_unknown_parser_kind_is_explicit_complete_equipment():
    s=pair('Гидравлический горизонтальный пресс NEDMACH N-HP40','N-HP40','NEDMACH')
    assert assess_pair_v22(s,s)['confirmed']

def test_machine_with_controller_is_not_controller_for_machine():
    s=pair('Гидравлические гильотинные ножницы с контроллером SB12/3200','SB12/3200','Beka-Mak')
    assert assess_pair_v22(s,s)['confirmed']
    component=pair('Контроллер для гильотинных ножниц SB12/3200','SB12/3200','Beka-Mak')
    assert not assess_pair_v22(component,s)['confirmed']

@pytest.mark.parametrize('name',[
 'Губки для фрезерного обрабатывающего центра V11 10000',
 'Ручка для фрезерного обрабатывающего центра V11 10000',
 'Контроллер фрезерного обрабатывающего центра V11 10000',
 'Патрон для фрезерного обрабатывающего центра V11 10000',
 'Вращающийся центр для фрезерного обрабатывающего центра V11 10000',
 'Комплект оснастки для фрезерного обрабатывающего центра V11 10000',
 'Модуль фрезерного обрабатывающего центра V11 10000',
 'Панель фрезерного обрабатывающего центра V11 10000',
 'Кабель фрезерного обрабатывающего центра V11 10000',
 'Держатель фрезерного обрабатывающего центра V11 10000',
])
def test_primary_component_never_becomes_complete_machine(name):
    target=pair('Фрезерный обрабатывающий центр BLIN V11 10000')
    assert not assess_pair_v22(pair(name),target)['confirmed']

@pytest.mark.parametrize('category',['Saw blades','Band saw blades','Оснастка / Ленточнопильные станки'])
def test_category_does_not_turn_blade_or_parts_into_machine(category):
    s=pair('BMSY-613VT','BMSY-613VT','Beka-Mak');s['source_category']=category
    t=pair('Ленточнопильный станок BMSY-613VT','BMSY-613VT','Beka-Mak')
    assert not assess_pair_v22(s,t)['confirmed']

def test_bandsaw_category_does_not_override_explicit_circular_saw():
    s=pair('Дисковая пила CUT250','CUT250','Optimum');s['source_category']='Ленточнопильные станки'
    t=pair('Ленточнопильный станок CUT250','CUT250','Optimum')
    assert not assess_pair_v22(s,t)['confirmed']

def test_automation_modes_remain_distinct_not_substring_alias():
    s=pair('Автоматический ленточнопильный станок BMSO230','BMSO230','Beka-Mak')
    t=pair('Полуавтоматический ленточный станок BMSO230','BMSO230','Beka-Mak')
    t['source_category']='Ленточнопильные станки'
    proof=assess_pair_v22(s,t)
    assert not proof['confirmed'] and 'V22_EXPLICIT_AUTOMATION_CONTRADICTION' in proof['blocking_reasons']

@pytest.mark.parametrize('left,right',[
 ('LX20 Pro','LX20 NEW'),('PP-13D NEW','PP-13D'),('TU2304V','TU2304'),
 ('45/500S+','45/500S'),('50/650S+','50/650S'),('M200 ATC+C','M200 ATCC'),
])
def test_new_kind_rule_cannot_erase_full_execution(left,right):
    s=pair('Фрезерный обрабатывающий центр '+left,left,'Optimum')
    t=pair('Фрезерный обрабатывающий центр '+right,right,'Optimum')
    assert not assess_pair_v22(s,t)['confirmed']

def test_new_kind_rule_retains_same_role_power_guard():
    s=pair('Фрезерный обрабатывающий центр BLIN V11 10000');t=deepcopy(s)
    s['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value='5.5',unit='кВт')]
    t['observed_properties']=[dict(name='Мощность главного двигателя, Вт',value='55000',unit='Вт')]
    assert 'SAME_ROLE_TYPED_CONTRADICTION' in assess_pair_v22(s,t)['blocking_reasons']
    t['observed_properties']=[]
    assert assess_pair_v22(s,t)['confirmed']

def test_duplicate_canonical_stays_review_and_inputs_unchanged():
    s=pair('Фрезерный обрабатывающий центр BLIN V11 10000');copy=deepcopy(s)
    targets={'1':{**s,'sterbrust_product_id':'1'},'2':{**s,'sterbrust_product_id':'2'}}
    result=PolicyV22Index(targets).evaluate(s)
    assert result['confirmed'] is None and result['blocking_reasons']==['CANONICAL_IDENTITY_NOT_UNIQUE']
    assert s==copy

def test_all_original_35_operator_calibrations_pass_v22():
    import json
    from pathlib import Path
    data=json.loads((Path(__file__).parent/'fixtures/kami_v21_operator_calibrations.json').read_bytes())
    assert len(data['cases'])==35
    for case in data['cases']:
        index=PolicyV22Index(data['targets'])
        chosen=index.evaluate(case['source'])['confirmed']
        assert chosen and chosen['sterbrust_product_id']==case['expected_id'],case['external_id']

def test_explicit_canonical_category_mode_not_ignored_for_convenient_title():
    s=pair('Автоматический ленточнопильный станок BMSO460C','BMSO460C','Beka-Mak')
    t=pair('Двухколонная ленточная пила BMSO460C','BMSO460C','Beka-Mak')
    t['source_category']='Ленточнопильные / Полуавтоматические'
    result=assess_pair_v22(s,t)
    assert not result['confirmed'] and 'V22_EXPLICIT_CATEGORY_AUTOMATION_CONTRADICTION' in result['blocking_reasons']

@pytest.mark.parametrize('other',['Лазерный','Электроэрозионный','Сверлильный'])
def test_complete_centers_with_explicit_different_functions_not_equated(other):
    s=pair(other+' обрабатывающий центр BLIN V11 10000')
    t=pair('Фрезерный обрабатывающий центр BLIN V11 10000')
    assert not assess_pair_v22(s,t)['confirmed']

def test_auxiliary_automatic_tool_change_is_not_main_automation_mode():
    from universal_supplier.matching_policy_v22 import automation_mode
    assert automation_mode('Ручной станок с автоматической сменой инструмента')=='MANUAL'

def test_real_stratified_saved_positives_and_automation_counterexamples():
    import json
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/matching_v22_kind_calibrations.json').read_bytes())
    assert len(fixture['cases'])>=12 and len(fixture['counterexamples'])==5
    for case in fixture['cases']:
        chosen=PolicyV22Index(case['targets']).evaluate(case['source'])['confirmed']
        assert chosen and chosen['sterbrust_product_id']==case['expected_id']
        assert chosen['rule_id']==case['expected_rule']
    for case in fixture['counterexamples']:
        evaluated=PolicyV22Index(case['targets']).evaluate(case['source'])
        assert evaluated['confirmed'] is None and case['expected_blocker'] in evaluated['blocking_reasons']

@pytest.mark.parametrize('left,right,key',[
 ('Вертикальный фрезерный обрабатывающий центр','Горизонтальный фрезерный обрабатывающий центр','ORIENTATION'),
 ('Одноколонный гидравлический пресс','Двухколонный гидравлический пресс','FRAME'),
 ('Двухколонный ленточнопильный станок','Маятниковый ленточный станок','FRAME'),
])
def test_explicit_structural_execution_is_not_erased_by_kind_refinement(left,right,key):
    s=pair(left+' TEST-M200','TEST-M200','TEST');t=pair(right+' TEST-M200','TEST-M200','TEST')
    s['source_category']=t['source_category']='Ленточнопильные станки' if key=='FRAME' and 'ленточ' in left else ''
    proof=assess_pair_v22(s,t)
    assert not proof['confirmed'] and 'V22_EXPLICIT_'+key+'_CONTRADICTION' in proof['blocking_reasons']
