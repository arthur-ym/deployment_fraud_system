"""
FastAPI app to serve a machine-learning model for predictions.

Loads an XGBoost model from "artifacts/model.ubj" (relative to the
project root) and exposes a /predict endpoint that accepts JSON
with a feature mapping and returns predictions.
"""

import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import xgboost as xgb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.features import FEATURE_ORDER

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


@app.get("/")
def health() -> dict:
    """Return application health status."""
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    """
    Generate predictions for a single row of input features.

    Expects JSON like:
      {"X": {"feature_name": value, ...}}
    The endpoint will check that all required features are present,
    order them according to FEATURE_ORDER, and convert to a numeric list.
    """
    if model is None:
        raise HTTPException(status_code=500, detail="Model not loaded")

    try:
        ordered_values = check_and_order_dict(req.X)
        X = np.array(ordered_values, dtype=float).reshape(1, -1)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to convert features to numeric array: {exc}",
        ) from exc

    try:
        preds = model.predict_proba(X)[0, 1]
        return {"prediction": float(preds)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
