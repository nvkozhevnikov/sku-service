"""Actual pure-policy fixture evaluations for the operator's regression ledger."""
import json
from tests.test_matching_policy_v2 import row
from universal_supplier.matching_policy_v2 import assess_pair,normalize_identity_model
from scripts.propose_kami_policy_v2 import OUT


def main():
    cases=[('LX20 Pro','LX20 NEW',None,None),('PP-13D NEW','PP-13D',None,None),
        ('ETM-16U','ETM-16U','Контроллер ETM-16U','Станок ETM-16U'),
        ('ETM-16U','ETM-16U','Ручка ETM-16U','Станок ETM-16U'),
        ('HCV125','HCV125','Губки HCV125','Тиски HCV125'),('TU2304V','TU2304',None,None),
        ('AHS20/35','AHS20/50',None,None),('45/500S+','45/500S',None,None),('50/650S+','50/650S',None,None),
        ('X+','X',None,None),('X+Y','XY',None,None),('X+Y','X',None,None)]
    results=[]
    for a,b,an,bn in cases:
        proof=assess_pair(row(a,an),row(b,bn))
        assert not proof['confirmed']
        results.append(dict(source_model=a,canonical_model=b,source_name=an or a,canonical_name=bn or b,
            source_key=normalize_identity_model(a),canonical_key=normalize_identity_model(b),status='PASS_NON_AUTO',
            blockers='; '.join(proof['blocking_reasons'])))
    for a,b in [('5.5','55000'),('11','1100'),('7.5','7.5')]:
        s=row();t=row();s['observed_properties']=[dict(name='Мощность главного двигателя, кВт',value=a,unit='кВт')]
        t['observed_properties']=[dict(name='Мощность главного двигателя, Вт',value=b,unit='Вт')]
        proof=assess_pair(s,t);assert not proof['confirmed']
        results.append(dict(source_model=a+'kW',canonical_model=b+'W',source_name=s['name'],canonical_name=t['name'],
            source_key=proof['evidence']['characteristics']['contradictions'][0]['source']['values'][0],
            canonical_key=proof['evidence']['characteristics']['contradictions'][0]['canonical']['values'][0],status='PASS_NON_AUTO',
            blockers='; '.join(proof['blocking_reasons'])))
    (OUT/'V2_1_NEGATIVE_FIXTURE_RESULTS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    print('Negative ledger PASS',len(results))


if __name__=='__main__':main()
