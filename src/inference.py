"""
FastAPI app to serve a machine-learning model for predictions.

This module loads a model from "artifacts/model.ubj" (relative to the project root)
and exposes a /predict endpoint that accepts JSON {"X": [values]} and returns predictions.
"""

from typing import List
import os
import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager

# Determine model path relative to this file:
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_PATH = os.path.join(ROOT, "artifacts", "model.ubj")

model = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler: load model on startup."""
    global model
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(f"Model file not found at: {MODEL_PATH}")
    try:
        model = joblib.load(MODEL_PATH)
    except Exception as exc:
        raise RuntimeError(f"Failed to load model from {MODEL_PATH}: {exc}") from exc
    try:
        yield
    finally:
        # optional cleanup
        model = None

app = FastAPI(title="Deployment Fraud System - Inference", lifespan=lifespan)

class PredictRequest(BaseModel):
    X: List[float]

@app.get("/")
def health() -> dict:
    return {"status": "ok", "model_loaded": model is not None}

@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    if model is None:
        raise HTTPException(status_code=500, detail="Model not loaded")
    try:
        X = np.array(req.X).reshape(-1, 1)
        preds = model.predict(X)
        return {"prediction": preds.tolist()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    # Run with: python src/inference.py
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
