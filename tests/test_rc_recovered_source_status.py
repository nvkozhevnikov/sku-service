from universal_supplier.control_plane.unified_jobs import supplier_snapshot_status


def test_rows_alone_do_not_close_recovery_gate():
    assert supplier_snapshot_status('vekprom',6834,0)=='BLOCKED_MISSING_VERIFIED_SOURCE_DATA'
    assert supplier_snapshot_status('intervesp',1642,0)=='HISTORICAL_ONLY_NEW_SNAPSHOT_BLOCKED'


def test_incomplete_import_or_missing_card_keeps_gate_closed():
    assert supplier_snapshot_status('vekprom',6834,6833)=='BLOCKED_MISSING_VERIFIED_SOURCE_DATA'
    assert supplier_snapshot_status('intervesp',1641,1642)=='HISTORICAL_ONLY_NEW_SNAPSHOT_BLOCKED'


def test_complete_verified_cohort_closes_only_source_gate():
    assert supplier_snapshot_status('vekprom',6834,6834)=='AVAILABLE'
    assert supplier_snapshot_status('intervesp',1642,1642)=='AVAILABLE'
    assert supplier_snapshot_status('kami',5264,0)=='AVAILABLE'
