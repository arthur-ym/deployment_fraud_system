"""
FastAPI app to serve a machine-learning model for predictions.

Loads an XGBoost model from "artifacts/model.ubj" (relative to the
project root) and exposes a /predict endpoint that accepts JSON
{"X": [values]} and returns predictions.
"""

import os
from contextlib import asynccontextmanager

import numpy as np
import xgboost as xgb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
    X: list


@app.get("/")
def health() -> dict:
    """Return application health status."""
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    """Generate predictions for a single row of input features."""
    if model is None:
        raise HTTPException(
            status_code=500,
            detail="Model not loaded",
        )

    try:
        X = np.array(req.X, dtype=float).reshape(1, -1)
        preds = model.predict_proba(X)[0, 1]

        return {"prediction": preds.tolist()}

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
