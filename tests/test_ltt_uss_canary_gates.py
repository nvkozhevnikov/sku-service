from scripts.run_ltt_uss_live_canary import gate


def sample():
    rows=[dict(outcome='PERSISTED',price_state='numeric_public',availability='InStock',kind='machine') for _ in range(20)]
    rows[0].update(price_state='price_on_request',availability='OutOfStock',kind='tool')
    return rows


def test_recognized_tool_kind_satisfies_non_machine_coverage():
    result=gate(sample(),'ltt_sib')
    assert result['status']=='LIVE_CANARY_PASS'
    assert result['coverage']['non_machine']==1


def test_block_is_never_canary_pass():
    rows=sample()
    rows[-1]['outcome']='SOURCE_BLOCKED'
    assert gate(rows,'ltt_sib')['status']=='LIVE_CANARY_BLOCKED'


def test_missing_unavailable_is_explicit_gate_failure():
    rows=sample()
    rows[0]['availability']='InStock'
    assert 'COVERAGE_MISSING:unavailable' in gate(rows,'ltt_sib')['issues']
