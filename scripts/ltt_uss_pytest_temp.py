"""Windows sandbox temp fixture: ordinary inherited ACL, isolated new paths.

The built-in Python3.14/pytest mode0700 directory denies sandbox access here.
No tests/assertions are changed; keep generated synthetic files as evidence.
"""
from pathlib import Path
from uuid import uuid4
import pytest

@pytest.fixture
def tmp_path():
    root = Path(__file__).resolve().parents[1] / 'reports/LTT_USS_INTEGRATION_2026-10-13/OFFLINE_RUN2_2026-10-15/TEST_TEMP_COMPAT'
    root.mkdir(parents=True, exist_ok=True)
    path = root / str(uuid4())
    path.mkdir(mode=0o777)
    return path
