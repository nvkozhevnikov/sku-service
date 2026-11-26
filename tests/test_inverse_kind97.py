"""Frozen explicit97/4 and all protected inverse cohorts, plus ownership negatives."""
from pathlib import Path
import json
import pytest
from universal_supplier.adapters.ltt_uss_html import source_evidence

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/LTT_USS_INTEGRATION_2026-10-13'
AUDIT=BASE/'INVERSE_KIND_IMPACT_SWEEP_2026-10-16'
B=BASE/'INVERSE_KIND_97_BATCH_2026-10-16'
def load(p):return json.loads(p.read_text('utf8'))
ROWS=load(AUDIT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows']
TERMINAL=load(B/'AUTHORIZED_TERMINAL4.json')['rows']
PREVIOUS=load(BASE/'PRODUCT_KIND_29_RESUME_2026-10-16/PRODUCT_KIND_29_MANIFEST.json')['rows']
VALID=[a for a in load(BASE/'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE']

def kind(row):
    d=load(B/'PARSED_ROWS'/row['supplier']/f"{row['index']:06d}.json")
    p=d['parsed_product'];inputs=d['classifier_inputs']
    return source_evidence(inputs['name'],inputs['category'],p['technical_properties'],p['enrichment_evidence']['availability_raw'],p['source_brand'],own_description=inputs['own_description'])['product_kind']

@pytest.mark.parametrize('row',ROWS,ids=lambda a:f"{a['supplier']}_{a['index']}_{a['semantic_decision']}")
def test_inverse97_operator106_valid597(row):
    new=kind(row)
    if row['semantic_decision']=='CONFIRMED_FALSE_NON_MACHINE_COMPLETE_MACHINE':
        assert new['product_kind']=='machine' and new['confidence']=='HIGH' and new!=row['current_kind']
    else:
        # Later operator-authorized disk10 transition: preserve the old evidence
        # and require both its exact before-kind and the explicitly gated after.
        from scripts.disk_alias_batch import S,AUTH_SHA,r
        assert r.digest((S/'AUTHORIZED_10_ROWS.json').read_bytes())==AUTH_SHA
        changed={(a['supplier'],a['index']):a for a in load(S/'DISK_ALIAS_10_MANIFEST.json')['rows']}
        key=row['supplier'],row['index']
        if key in changed:
            assert row['current_kind']==changed[key]['old_kind']
            assert new==changed[key]['new_kind'] and new!=row['current_kind']
        else:
            from scripts.final_hotspot36 import S as U,AUTH_SHA
            assert r.digest((U/'AUTHORIZED_36_ROWS.json').read_bytes())==AUTH_SHA
            final={(a['supplier'],a['index']):a for a in load(U/'FINAL_HOTSPOT36_MANIFEST.json')['rows']}
            if key in final:
                assert row['current_kind']==final[key]['old_kind']
                assert new==final[key]['new_kind'] and new!=row['current_kind']
            else:assert new==row['current_kind']

@pytest.mark.parametrize('row',TERMINAL,ids=lambda a:f"terminal{a['index']}")
def test_terminal_four_machine_kind_only(row):
    assert kind(row)['product_kind']=='machine' and kind(row)['confidence']=='HIGH'
    assert load(B/'PARSED_ROWS'/row['supplier']/f"{row['index']:06d}.json")['entry']['outcome']==row['outcome']

@pytest.mark.parametrize('row',PREVIOUS,ids=lambda a:f"protected29_{a['supplier']}_{a['index']}")
def test_previous29_exact_kind_preserved(row):assert kind(row)==row['new_kind']

@pytest.mark.parametrize('row',VALID,ids=lambda a:f"valid_machine_{a['supplier']}_{a['index']}")
def test_prior362_exact_kind_preserved(row):assert kind(row)==row['raw_enrichment_evidence']['product_kind']

@pytest.mark.parametrize('index',[941,958])
def test_sib_operator_trimmers_preserved(index):
    d=load(B/'PARSED_ROWS/ltt_sib'/f"{index:06d}.json")
    assert kind(dict(supplier='ltt_sib',index=index))==d['old_kind']

@pytest.mark.parametrize('title,category,expected',[
 ('Форматный станок с роликовой кареткой','Форматные станки','machine'),
 ('Круглопалочный станок с двойными роликами','Станки','machine'),
 ('Фрезерный обрабатывающий центр VMC999 с роликовой кареткой','Центры','machine'),
 ('5-осевой обрабатывающий центр AB500','Обрабатывающие центры','machine'),
 ('Ролик с эксцентриком на форматно-раскроечный станок','Запчасти','spare'),
 ('Ролики для форматного станка','Запчасти','spare'),
 ('Ремень для фрезерного станка','Запчасти','spare'),
 ('Фреза для сверлильно-присадочного центра H-900','Фрезы','tooling'),
 ('Патрон для фрезерного обрабатывающего центра VMC999','Оснастка','tooling'),
 ('Вращающийся центр МК3','','unknown'),
 ('Дисковая пила250х30х3 Freud LU3D','mach/sagen','disk'),
 ('Дисковая пила 250х30х3 Freud LU3D','mach/sagen','disk'),
 ('Пила узла торцовки кромкооблицовочного станка','Кромкооблицовочные станки','saw_blade'),
 ('Гильотина для обрезки кромки на кромкооблицовочный станок','Кромкооблицовочные станки','cutting_attachment'),
 ('Ручной фрезер для снятия свесов M1P-FF02-6','Инструменты','tool'),
 ('Ножовочный станок с пильным диском','Станки','machine'),
 ('Станок с ножевым валом','Станки','machine'),
 ('Станок с вакуумным насосом','Станки','machine'),
])
def test_generic_owned_head_and_real_parts(title,category,expected):
    assert source_evidence(title,category,(),'')['product_kind']['product_kind']==expected

def test_cohort_counts_and_unique_authorization():
    assert len(ROWS)==800 and sum(a['semantic_decision']=='CONFIRMED_FALSE_NON_MACHINE_COMPLETE_MACHINE' for a in ROWS)==97
    assert sum(a['semantic_decision']=='OPERATOR_REVIEW_REQUIRED' for a in ROWS)==106
    assert sum(a['semantic_decision']=='VALID_NON_MACHINE' for a in ROWS)==597
    assert len(TERMINAL)==4 and len(PREVIOUS)==29 and len(VALID)==362
    assert len({(a['supplier'],a['external_id']) for a in load(B/'AUTHORIZED_97_ROWS.json')['rows']})==97
