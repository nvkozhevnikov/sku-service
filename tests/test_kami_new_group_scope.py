from tests.test_new_group_proposals import candidate
from universal_supplier.new_group_proposals import propose_new_groups


def test_kami_scope_is_opt_in_and_keeps_execution_guards():
    rows = [candidate('kami', 'site:1'), candidate('intervesp', 'known')]
    assert len(propose_new_groups(rows, allowed_sources={'kami', 'intervesp'})['groups']) == 1
    assert not propose_new_groups(rows)['groups']
    rows[0]['model'] = 'BMS-230DGH'
    rows[0]['execution'] = 'BMS-230DGH'
    assert not propose_new_groups(rows, allowed_sources={'kami', 'intervesp'})['groups']
