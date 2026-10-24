import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def payloads():
    path_data = Path("artifacts/payload_test.json")

    with path_data.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
