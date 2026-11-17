from datetime import datetime, timezone

import pytest

from universal_supplier.control_plane.presentation import moscow_datetime, supplier_display_name, job_message


@pytest.mark.parametrize('value, expected', [
    ('2026-11-16T23:30:12.123456+00:00', '17.11.2026, 02:30'),
    ('2026-10-28T11:19:21.303266+03:00', '28.10.2026, 11:19'),
    ('2026-11-17T05:00:00Z', '17.11.2026, 08:00'),
    (datetime(2026, 11, 17, 5, tzinfo=timezone.utc), '17.11.2026, 08:00'),
    (None, 'Нет данных'),
    ('not a date', 'Нет данных'),
])
def test_moscow_dates_without_wire_timestamp(value, expected):
    assert moscow_datetime(value) == expected


def test_technical_success_is_not_shown_as_wire_status():
    assert job_message('status=succeeded; termination_reason=none; discovery_complete=true') == ''


def test_uncertain_request_and_server_failure_remain_visible_in_russian():
    assert job_message('AUTOMATION_UNKNOWN_HTTP_OUTCOME') == 'Исход запроса не подтверждён'
    assert job_message('HTTP_500') == 'Поставщик вернул ошибку'


def test_supplier_reference_name_is_readable_without_changing_identity():
    row = {'code': 'beka_mak_tr', 'name': 'Bekamak manufacturer reference'}
    assert supplier_display_name(row) == 'Bekamak · справочник'
    assert row['code'] == 'beka_mak_tr'
