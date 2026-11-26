"""Every authorized raw source case, protected metadata and unseen role negatives."""
from scripts.final_hotspot36 import S,T,H,r,o,AUTH_SHA
from universal_supplier.adapters.ltt_uss_html import source_evidence
from dataclasses import asdict
import pytest,copy

AUTHORIZED=r.load(S/'AUTHORIZED_36_ROWS.json')['rows']
ALL={(a['entry']['supplier'],a['entry']['index']):a for a in r.load(H/'ALL_CURRENT_ROWS.json')['rows']}
PROTECTED=[dict(a,cohort=label) for label,c in r.load(H/'HOTSPOT_PROTECTED_COHORTS.json')['cohorts'].items() for a in c['rows']]

def key(a):
    e=a.get('entry',a);return e['supplier'],e['index']

def actual(a):
    inputs=ALL[key(a)]['classifier_inputs']
    return source_evidence(inputs['name'],inputs['category'],(),'',own_description=inputs['own_description'])['product_kind']

@pytest.mark.parametrize('row',AUTHORIZED,ids=lambda a:f"source_{a['supplier']}_{a['row']}_{a['stable_id']}")
def test_exact36_raw_source_kind_only(row):
    e=row['entry'];j=r.load(o.ROOT/e['journal']);m=j['result'];body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes();assert r.digest(body)==row['evidence_sha256']
    parsed=asdict(r.parse(e['supplier'],body,m['final_url']));old=r.load(T/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json")['parsed_product'];kind=parsed['enrichment_evidence']['product_kind']
    assert kind['product_kind']==row['expected_existing_kind'] and kind['source']=='sold_object_head' and kind['matched_alias'] and kind['confidence']=='HIGH'
    assert kind!=row['current_kind'] and old['enrichment_evidence']['product_kind']==row['current_kind']
    parsed['enrichment_evidence']['product_kind']=row['current_kind'];assert r.encoded(parsed)==r.encoded(old)
    if key(row)==('ltt_msk',176):assert row['stable_id']=='1770' and kind['product_kind']=='power_feeder'

@pytest.mark.parametrize('row',PROTECTED,ids=lambda a:f"{a['cohort']}_{key(a)}")
def test_every_protected_current_kind_exact(row):assert actual(row)==row['current_kind']

@pytest.mark.parametrize('title,category,expected',[
 ('Автоподатчик QZ990 (8 роликов)','Оснащение','power_feeder'),
 ('Роликовый автоподатчик ZX900','Оснащение','power_feeder'),
 ('ZX900 (4 ролика) Автоподатчик универсальный','Оснащение','power_feeder'),
 ('Универсальный 3-х роликовый 8-ми скоростной автоподатчик ZX900','Оснащение','power_feeder'),
 ('Ролики для автоподатчиков','Запчасти','spare'),
 ('Ролик автоподатчика ZX900','Запчасти','spare'),
 ('Ремень для автоподатчика ZX900','Запчасти','spare'),
 ('Нож твердосплавный 19х19','Для столярных станков','knife'),
 ('Сменные ножи для нового станка','Для столярных станков','knife'),
 ('Направляющая для пилы с шариковыми подшипниками нижняя для станка','Для столярных станков','spare'),
 ('Универсальный эксцентриковый прижим','Для столярных станков','fixture'),
 ('Неопознанный предмет','Для столярных станков','unknown'),
 ('Столярный стол-верстак Q900','Оснащение','table'),
 ('Столярные верстаки','Оснащение','table'),
 ('Гидравлический подъемный стол Q900','','table'),
 ('Поворотный стол Q900','','table'),
 ('Столярная оснастка','','fixture'),
 ('Чиллер Q900','Чиллеры и расходные части к лазерам','chiller'),
 ('Зеркало D30','Чиллеры и расходные части к лазерам','consumable'),
 ('Шланг для чиллера Q900','Чиллеры и расходные части к лазерам','consumable'),
 ('Чиллер Q900','Другие объекты','unknown'),
])
def test_unseen_owned_objects_and_host_negatives(title,category,expected):
    assert source_evidence(title,category,(),'')['product_kind']['product_kind']==expected

def test_explicit_new_seal_and_archived_disk10():
    from scripts import final_hotspot36_seals as s
    assert s.verify(baseline_seal=s.ACCEPTED,proposed_transition=s.PROPOSED,working_hashes=s.named(s.PROPOSED))['working_code_hashes']==s.named(s.PROPOSED)
    assert s.verify(baseline_seal=s.ACCEPTED,working_hashes=s.named(s.ACCEPTED))['working_code_hashes']==s.named(s.ACCEPTED)
    with pytest.raises(AssertionError):s.verify(baseline_seal=s.ACCEPTED)
    with pytest.raises(AssertionError):s.verify(baseline_seal=s.ACCEPTED,proposed_transition='LATEST')
    assert r.digest((S/'AUTHORIZED_36_ROWS.json').read_bytes())==AUTH_SHA

def test_final_impact_exact_authorized_scope_and_no_terminal_delta():
    a=r.load(S/'FULL_IMPACT.json');assert a['status']=='PASS' and a['scope']==2634
    assert (a['changed_persisted'],a['changed_terminal'])==(36,0)
    assert a['non_kind_payload_delta']==a['terminal_kind_delta']==a['terminal_outcome_delta']==a['additional_persisted_kind_delta']==0
