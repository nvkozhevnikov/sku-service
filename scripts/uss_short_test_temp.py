"""Process-only short fixture root; assertions and production code unchanged."""
from pathlib import Path
from uuid import uuid4
import os
import pytest

@pytest.fixture
def tmp_path():
    root=Path(os.environ['USS_TEST_TEMP_ROOT']).resolve()
    root.mkdir(parents=True,exist_ok=True)
    path=root/str(uuid4());path.mkdir(mode=0o777)
    return path
