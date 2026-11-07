import json
import math

import pytest
from fastapi.testclient import TestClient

from src.inference import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as client:
        yield client


def _request_logs(stdout: str) -> list[dict]:
    logs = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        record = json.loads(line)
        if record.get("event") == "request":
            logs.append(record)
    return logs


def test_prediction_is_probability(client, payloads):
    response = client.post(
        "/predict",
        json={"X": payloads[0]},
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"prediction"}
    assert response.headers["X-Request-ID"]

    prediction = response.json()["prediction"]

    assert isinstance(prediction, (int, float))
    assert math.isfinite(prediction)
    assert 0 <= prediction <= 1


def test_missing_feature_is_logged(client, payloads, capsys):
    features = dict(payloads[0])
    features.pop("amount_cents")

    response = client.post("/predict", json={"X": features})

    assert response.status_code == 400, response.text
    logs = _request_logs(capsys.readouterr().out)
    assert len(logs) == 1
    assert logs[0]["request_id"] == response.headers["X-Request-ID"]
    assert logs[0]["http_status"] == 400
    assert logs[0]["error_category"] == "invalid_features"
    assert logs[0]["path"] == "/predict"
    assert "amount_cents" in logs[0]["error_message"]


def test_health_check_is_not_a_request_metric(client, capsys):
    response = client.get("/")

    assert response.status_code == 200
    assert _request_logs(capsys.readouterr().out) == []


def test_prediction_is_stored(client, payloads, monkeypatch):
    stored: dict = {}

    class FakeS3:
        def put_object(self, **kwargs):
            stored["kwargs"] = kwargs

    monkeypatch.setenv("PREDICTIONS_BUCKET", "predictions-test")
    monkeypatch.setenv("MODEL_VERSION", "test-model")
    monkeypatch.setattr("src.telemetry._s3_client", FakeS3())

    response = client.post("/predict", json={"X": payloads[0]})

    assert response.status_code == 200, response.text
    put = stored["kwargs"]
    body = json.loads(put["Body"])
    request_id = response.headers["X-Request-ID"]
    assert put["Bucket"] == "predictions-test"
    assert put["ContentType"] == "application/json"
    assert put["Key"] == f"predictions/dt={body['timestamp'][:10]}/{request_id}.json"
    assert body["request_id"] == request_id
    assert body["status"] == "success"
    assert body["http_status"] == 200
    assert body["fraud_probability"] == response.json()["prediction"]
    assert body["predicted_class"] == (1 if body["fraud_probability"] >= 0.5 else 0)
    assert body["model_version"] == "test-model"
    assert body["error_category"] == "none"
    assert body["error_message"] is None
    assert body["features"]["amount_cents"] == payloads[0]["amount_cents"]


def test_storage_is_skipped_without_a_bucket(client, payloads, monkeypatch):
    called = {"n": 0}

    class FakeS3:
        def put_object(self, **kwargs):
            called["n"] += 1

    monkeypatch.delenv("PREDICTIONS_BUCKET", raising=False)
    monkeypatch.setattr("src.telemetry._s3_client", FakeS3())

    response = client.post("/predict", json={"X": payloads[0]})

    assert response.status_code == 200, response.text
    assert called["n"] == 0


def test_storage_failure_does_not_fail_the_request(
    client, payloads, monkeypatch, capsys
):
    class FakeS3:
        def put_object(self, **kwargs):
            raise RuntimeError("s3 down")

    monkeypatch.setenv("PREDICTIONS_BUCKET", "predictions-test")
    monkeypatch.setattr("src.telemetry._s3_client", FakeS3())

    response = client.post("/predict", json={"X": payloads[0]})

    assert response.status_code == 200, response.text
    stdout = capsys.readouterr().out
    storage_errors = [
        json.loads(line)
        for line in stdout.splitlines()
        if line.startswith("{") and json.loads(line).get("event") == "storage_error"
    ]
    assert len(storage_errors) == 1
    assert storage_errors[0]["request_id"] == response.headers["X-Request-ID"]
    assert "s3 down" in storage_errors[0]["error_message"]
