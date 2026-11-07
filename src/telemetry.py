"""One JSON log line per prediction, and an optional S3 copy of that record."""

import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

CLASS_THRESHOLD = 0.5

_s3_client = None


def model_version() -> str:
    return os.environ.get("MODEL_VERSION", "unknown")


def predictions_bucket() -> str:
    return os.environ.get("PREDICTIONS_BUCKET", "").strip()


def predicted_class(probability: float | None) -> int | None:
    """Map a probability to a class. The cutoff is only for the stored record."""
    if probability is None:
        return None
    return 1 if probability >= CLASS_THRESHOLD else 0


def record_predict(
    *,
    request_id: str,
    http_status: int,
    duration_ms: float,
    error_category: str,
    error_message: str | None,
    fraud_probability: float | None,
    features: dict[str, Any] | None,
) -> dict[str, Any]:
    """Print the request log and store the same facts when a bucket is configured."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    succeeded = http_status < 400 and error_category == "none"
    record = {
        "request_id": request_id,
        "timestamp": timestamp,
        "http_status": http_status,
        "status": "success" if succeeded else "error",
        "duration_ms": round(duration_ms, 3),
        "fraud_probability": fraud_probability,
        "predicted_class": predicted_class(fraud_probability),
        "model_version": model_version(),
        "error_category": error_category,
        "error_message": error_message,
        "features": features,
    }
    print(
        json.dumps(
            {
                "event": "request",
                "request_id": request_id,
                "timestamp": timestamp,
                "path": "/predict",
                "http_status": http_status,
                "duration_ms": record["duration_ms"],
                "error_category": error_category,
                "error_message": error_message,
            }
        ),
        flush=True,
    )
    _store_prediction(record)
    return record


def _store_prediction(record: dict[str, Any]) -> None:
    bucket = predictions_bucket()
    if not bucket:
        return
    key = f"predictions/dt={record['timestamp'][:10]}/{record['request_id']}.json"
    try:
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(record).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001 — a storage failure must not fail the API request
        print(
            json.dumps(
                {
                    "event": "storage_error",
                    "request_id": record["request_id"],
                    "error_message": str(exc),
                }
            ),
            flush=True,
        )


def _client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client
