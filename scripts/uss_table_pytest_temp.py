"""Isolated inherited-ACL Windows test directories; no historical receipt writes."""
from pathlib import Path
from uuid import uuid4
import pytest

@pytest.fixture
def tmp_path():
    root=Path(__file__).resolve().parents[1]/'reports/LTT_USS_INTEGRATION_2026-10-13/USS_TABLE_FIX_2026-10-15/TEST_TEMP'
    root.mkdir(parents=True,exist_ok=True)
    path=root/str(uuid4());path.mkdir(mode=0o777)
    return path
