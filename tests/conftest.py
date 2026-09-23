import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
