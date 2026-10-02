from universal_supplier.execution_evidence import axis_execution_guard,axis_signature


def test_axes_not_inferred_from_model_digits_or_axis_travel():
    assert axis_signature('HV95',{'Перемещение по оси X':'500'})=={'axes':[],'interpolation':[]}


def test_explicit_equivalent_axis_labels():
    assert not axis_execution_guard('Пятиосевой центр HV8i',{},'HV8i 5 axis',{})['blocked']


def test_unsupported_or_contradictory_axis_execution_is_not_confirmed():
    assert axis_execution_guard('HV8i 5 axis',{},'HV8i',{})['reason']=='axis_execution_evidence_incomplete'
    assert axis_execution_guard('HV8i 5 axis',{},'HV8i 4 axis',{})['contradiction']
    assert axis_execution_guard('HV8i 4+1',{},'HV8i 5 axis',{})['blocked']
    assert axis_execution_guard('HV8i 5 axis',{'Количество осей':'4'},'HV8i 5 axis',{})['contradiction']
def test_used_condition_is_not_comparable_without_canonical_condition_evidence():
    from universal_supplier.execution_evidence import condition_execution_guard, condition_signature
    assert condition_execution_guard('BODOR C3 (б/у)',{},'BODOR C3',{})['blocked']
    assert condition_execution_guard('BODOR C3 (б/у)',{},'BODOR C3 (б/у)',{})['blocked'] is False
    assert condition_signature('LX20 NEW')=='unspecified'
    assert condition_signature('Новый станок LX20 Pro')=='new'
