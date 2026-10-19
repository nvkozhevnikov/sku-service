from dataclasses import replace
from sterbrust_matching.matching import Product, _identity_conflicts

class NoReprTuple(tuple):
    def __str__(self):raise AssertionError('Tuple rendering must use scalar values')
    def __repr__(self):raise AssertionError('Tuple repr must not drive voltage evidence')

def test_voltage_guard_expands_multivalues_without_python_repr():
    source=Product('S','Станок TEST BD-7','TEST','BD-7',properties={'voltage':NoReprTuple(('220 V','380 V'))})
    target=Product('101','Станок TEST BD-7','TEST','BD-7',properties={'voltage':'220 V'})
    result=_identity_conflicts(source,target)
    assert result['voltage_execution']['source']=='mixed'
    assert result==_identity_conflicts(replace(source,properties={'voltage':('220 V','380 V')}),target)
