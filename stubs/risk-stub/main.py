"""Fake risk: always allows."""
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "stub"}


@app.post("/score")
def score(body: dict):
    return {"score": 0.0, "decision": "allow", "model_version": "stub", "source": "ai"}