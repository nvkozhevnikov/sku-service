"""Exact authorized10+terminal1 and all accepted disk/protected regressions."""
from scripts.disk_alias_batch import S,B,D,r
from universal_supplier.adapters.ltt_uss_html import source_evidence
from sterbrust_matching.product_identity import classify_product_kind
import pytest

def key(a):
    e=a.get('entry',a);return e['supplier'],e['index']

def actual(a):
    supplier,index=key(a);d=r.load(B/'PARSED_ROWS'/supplier/f'{index:06d}.json');p=d['parsed_product'];i=d['classifier_inputs']
    return source_evidence(i['name'],i['category'],p['technical_properties'],p['enrichment_evidence']['availability_raw'],p['source_brand'],own_description=i['own_description'])['product_kind']

TARGETS=r.load(S/'DISK_ALIAS_10_MANIFEST.json')['rows']
VALID=r.load(D/'DISK_ALIAS_VALID_REGRESSION_COHORT.json')['rows']
CONTEXT=[a for a in r.load(D/'DISK_ALIAS_SEMANTIC_DECISIONS.json')['rows'] if a['current_non_disk_winner_preserved']]
PROTECTED=[dict(a,cohort=label) for label,v in r.load(D/'PROTECTED_COHORTS_RISK.json')['cohorts'].items() for a in v['rows']]

@pytest.mark.parametrize('row',TARGETS,ids=lambda a:f"target_{a['supplier']}_{a['index']}")
def test_exact_ten_existing_targets_and_generic_evidence(row):
    new=actual(row);assert new==row['new_kind'] and new!=row['old_kind']
    authorization=next(a for a in r.load(S/'AUTHORIZED_10_ROWS.json')['rows'] if key(a)==key(row))
    assert new['product_kind']==authorization['expected_existing_kind']
    assert new['source']=='sold_object_head' and new['matched_alias'] and new['confidence']=='HIGH'

@pytest.mark.parametrize('row',VALID,ids=lambda a:f"valid_{key(a)}")
def test_true137_exact_metadata_preserved(row):assert actual(row)==row['current_kind']

@pytest.mark.parametrize('row',CONTEXT,ids=lambda a:f"context_{key(a)}")
def test_context93_exact_metadata_preserved(row):
    # Explicit later authorized36 transition; preserve both exact before and
    # after witnesses for intersections, every other context output stays exact.
    from scripts.final_hotspot36 import S as U,AUTH_SHA as authorized_sha
    assert r.digest((U/'AUTHORIZED_36_ROWS.json').read_bytes())==authorized_sha
    changed={key(a):a for a in r.load(U/'FINAL_HOTSPOT36_MANIFEST.json')['rows']}
    if key(row) in changed:
        transition=changed[key(row)];assert row['current_kind']==transition['old_kind']
        assert actual(row)==transition['new_kind'] and transition['new_kind']!=transition['old_kind']
    else:assert actual(row)==row['current_kind']

@pytest.mark.parametrize('row',PROTECTED,ids=lambda a:f"{a['cohort']}_{key(a)}")
def test_previous_protected_cohorts_exact_metadata_preserved(row):assert actual(row)==row['current_kind']

@pytest.mark.parametrize('index',[941,958])
def test_two_sib_held_cases(index):
    assert actual(dict(supplier='ltt_sib',index=index))==r.load(B/'PARSED_ROWS/ltt_sib'/f'{index:06d}.json')['parsed_product']['enrichment_evidence']['product_kind']

def test_terminal_editorial_unknown_without_eligibility_change():
    from scripts.disk_alias_batch import o
    e=next(a for a in r.load(S/'EFFECTIVE_ROW_PLAN.json')['rows'] if key(a)==('uss_stanko',225))
    j=r.load(o.ROOT/e['journal']);assert o.extract(j)==('AMBIGUOUS',None,None)
    assert actual(dict(supplier='uss_stanko',index=225))==dict(product_kind='unknown',matched_alias='',source='none',confidence='UNKNOWN')
    assert r.load(B/'PARSED_ROWS/uss_stanko/000225.json')['entry']['outcome']==e['outcome']=='AMBIGUOUS'

@pytest.mark.parametrize('title,category,expected',[
 ('ТЭН круглый для кромкооблицовочного станка','Для круглопильных станков','spare'),
 ('Нагревательный элемент круглый','Для круглопалочных станков','spare'),
 ('Лазерный указатель X900','Для круглопильных станков','accessory'),
 ('Подающее колесо для машины','Для круглопалочных станков','spare'),
 ('Втулка новой модели','Для круглопалочных станков','spare'),
 ('Нож для круглопалочного станка','Для круглопалочных станков','knife'),
 ('Круглый стол','','unknown'),('Круглая опора','','unknown'),
 ('Круглый ТЭН','','unknown'),
 ('Шлифовальный круг','','disk'),('Полировальный круг','','disk'),
 ('Круга шлифовального набор','','disk'),('Круги абразивные','','disk'),
 ('Дисковая пила 250х30х2.5 z80','Инструмент','disk'),
 ('Пила дисковая 100х20х3 z24','Инструмент','disk'),
 ('Нож дисковый 100х20','Инструмент','disk'),
 ('CBN 125x32','','disk'),('SDC 150x32','','disk'),
 ('Круглопильный станок с ТЭНом','Станки','machine'),
 ('Рекомендуемые режимы резания дисковых пил','mach/sagen','unknown'),
])
def test_semantic_noun_context_and_untouched_aliases(title,category,expected):
    assert source_evidence(title,category,(),'')['product_kind']['product_kind']==expected

def test_new_active_seal_and_old_archive_both_proven():
    from scripts import disk_alias_seals as s
    assert s.verify(baseline_seal=s.ACCEPTED,proposed_transition=s.PROPOSED,working_hashes=s.named(s.PROPOSED))['working_code_hashes']==s.named(s.PROPOSED)
    assert s.verify(baseline_seal=s.ACCEPTED,working_hashes=s.named(s.ACCEPTED))['working_code_hashes']==s.named(s.ACCEPTED)
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):s.verify(baseline_seal=s.ACCEPTED)
    with pytest.raises(AssertionError,match='UNAPPROVED'):s.verify(baseline_seal=s.ACCEPTED,proposed_transition='LATEST')

def test_exact_impact_and_counts():
    a=r.load(S/'FULL_IMPACT.json');assert a['status']=='PASS' and a['scope']==2634
    assert (a['changed_persisted'],a['changed_terminal'])==(10,1)
    assert a['non_kind_payload_delta']==a['additional_persisted_kind_delta']==a['additional_terminal_kind_delta']==a['terminal_outcome_delta']==0
    assert len(TARGETS)==10 and len(VALID)==137 and len(CONTEXT)==93 and len(PROTECTED)==603
