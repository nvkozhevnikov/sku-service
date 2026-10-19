from datetime import datetime,timezone,timedelta
from universal_supplier.control_plane.unified_jobs import FrozenDispatch
import pytest

def test_disabled_schedule_has_no_effect_and_eat_clock_maps_to_utc_due():
    d=FrozenDispatch(lambda:(_ for _ in ()).throw(AssertionError('No real DB')))
    d.sources=lambda:[{'key':'supplier:ltt_msk'}]
    calls=[]
    def enqueue(key,origin,slot):calls.append((key,origin,slot));return {'status':'queued'}
    d.enqueue=enqueue
    clock=datetime(2026,10,19,11,0,tzinfo=timezone(timedelta(hours=3)))
    assert d.evaluate_due(clock)==[] and calls==[]
    assert len(d.evaluate_due(clock,simulate_enabled=True))==1
    assert calls[0][2]==datetime(2026,10,19,8,0,tzinfo=timezone.utc)
    assert d.evaluate_due(clock.replace(hour=12),simulate_enabled=True)==[]
    with pytest.raises(ValueError):d.evaluate_due(datetime(2026,10,19,8))
