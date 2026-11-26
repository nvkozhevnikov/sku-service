"""Historic LIVE11 evidence must replay at its recorded calendar, without editing scopes/deadlines."""
from datetime import datetime,timezone


def freeze_runtime_utc(monkeypatch):
    from universal_supplier.control_plane import source_runtime,source_batch,source_recovery
    class RecordedDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,10,23,14,1,tzinfo=timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)
    monkeypatch.setattr(source_runtime,'datetime',RecordedDateTime)
    monkeypatch.setattr(source_batch,'datetime',RecordedDateTime)
    monkeypatch.setattr(source_recovery,'datetime',RecordedDateTime)
