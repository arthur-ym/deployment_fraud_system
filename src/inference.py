"""
FastAPI app to serve a machine-learning model for predictions.

Loads an XGBoost model from "artifacts/model.ubj" (relative to the
project root) and exposes a /predict endpoint that accepts JSON
with a feature mapping and returns predictions.
"""

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import xgboost as xgb
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from src.features import FEATURE_ORDER
from src.telemetry import record_predict

# Determine model path relative to this file
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_PATH = os.path.join(ROOT, "artifacts", "model.ubj")

model = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the XGBoost model on application startup."""
    global model

    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(f"Model file not found at: {MODEL_PATH}")

    try:
        model = xgb.XGBClassifier()
        model.load_model(MODEL_PATH)
    except Exception as exc:
        model = None
        raise RuntimeError(f"Failed to load model from {MODEL_PATH}: {exc}") from exc

    try:
        yield
    finally:
        model = None


app = FastAPI(
    title="Deployment Fraud System - Inference",
    lifespan=lifespan,
)


class PredictRequest(BaseModel):
    """Request model expects a mapping from feature name to value under `X`."""

    X: dict[str, Any]


def check_and_order_dict(features: dict[str, Any]) -> list[Any]:
    """
    Validate that `features` contains all keys in FEATURE_ORDER and
    return a list of values ordered according to FEATURE_ORDER.
    Raises HTTPException(400) if validation fails.
    """
    if not isinstance(features, dict):
        raise HTTPException(status_code=400, detail="X must be an object/dictionary")

    values: list[Any] = []
    missing: list[str] = []
    for name in FEATURE_ORDER:
        if name not in features:
            missing.append(name)
        else:
            values.append(features[name])

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required features: {', '.join(missing)}",
        )

    return values


@app.middleware("http")
async def observe_predict(request: Request, call_next):
    """Log /predict calls. Health checks are not usage metrics."""
    if request.url.path != "/predict":
        return await call_next(request)

    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.error_category = None
    request.state.error_message = None
    request.state.fraud_probability = None
    request.state.features = None
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        record_predict(
            request_id=request_id,
            http_status=500,
            duration_ms=(time.perf_counter() - started) * 1000,
            error_category="exception",
            error_message=str(exc),
            fraud_probability=None,
            features=getattr(request.state, "features", None),
        )
        raise

    category = getattr(request.state, "error_category", None)
    message = getattr(request.state, "error_message", None)
    status = response.status_code
    if category is None:
        if status < 400:
            category = "none"
        elif status < 500:
            category = "client_error"
            if message is None:
                message = (
                    "Request validation failed" if status == 422 else "Client error"
                )
        else:
            category = "server_error"
            if message is None:
                message = "Server error"

    record_predict(
        request_id=request_id,
        http_status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        error_category=category,
        error_message=message,
        fraud_probability=getattr(request.state, "fraud_probability", None),
        features=getattr(request.state, "features", None),
    )
    response.headers["X-Request-ID"] = request_id
    return response


def _fail(request: Request, status: int, category: str, message: str) -> None:
    request.state.error_category = category
    request.state.error_message = message
    raise HTTPException(status_code=status, detail=message)


@app.get("/")
def health() -> dict:
    """Return application health status."""
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/predict")
def predict(req: PredictRequest, request: Request) -> dict:
    """
    Generate predictions for a single row of input features.

    Expects JSON like:
      {"X": {"feature_name": value, ...}}
    The endpoint will check that all required features are present,
    order them according to FEATURE_ORDER, and convert to a numeric list.
    """
    request.state.features = req.X
    if model is None:
        _fail(request, 500, "server_error", "Model not loaded")

    try:
        ordered_values = check_and_order_dict(req.X)
        features = np.array(ordered_values, dtype=float).reshape(1, -1)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if detail.startswith(("Missing required features", "Failed to convert")):
            category = "invalid_features"
        else:
            category = "client_error"
        request.state.error_category = category
        request.state.error_message = detail
        raise
    except Exception as exc:
        message = f"Failed to convert features to numeric array: {exc}"
        request.state.error_category = "invalid_features"
        request.state.error_message = message
        raise HTTPException(status_code=400, detail=message) from exc

    try:
        probability = float(model.predict_proba(features)[0, 1])
    except Exception as exc:
        request.state.error_category = "model_error"
        request.state.error_message = str(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    request.state.fraud_probability = probability
    request.state.error_category = "none"
    return {"prediction": probability}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
