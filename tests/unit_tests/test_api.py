import math

import pytest
from fastapi.testclient import TestClient

from src.inference import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as client:
        yield client


def test_prediction_is_probability(client, payloads):
    response = client.post(
        "/predict",
        json={"X": payloads[0]},
    )

    assert response.status_code == 200, response.text

    prediction = response.json()["prediction"]

    assert isinstance(prediction, (int, float))
    assert math.isfinite(prediction)
    assert 0 <= prediction <= 1
