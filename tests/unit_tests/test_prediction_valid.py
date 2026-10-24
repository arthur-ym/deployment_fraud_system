import math

import requests

URL = "http://localhost:8000/predict"


def test_prediction_is_probability(payloads):
    response = requests.post(
        URL,
        json={"X": payloads[0]},
        timeout=10,
    )

    assert response.status_code == 200

    result = response.json()
    prediction = result["prediction"]  # Adjust to your API response key

    assert isinstance(prediction, (int, float))
    assert math.isfinite(prediction)
    assert 0 <= prediction <= 1
