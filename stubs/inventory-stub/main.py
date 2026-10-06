"""Fake inventory: always says yes. Lets B/D work before A is ready."""
import itertools
from fastapi import FastAPI

app = FastAPI()
ids = itertools.count(1)


@app.get("/health")
def health():
    return {"status": "stub"}


@app.post("/reservations")
def reserve(body: dict):
    return {"reservation_id": next(ids), "status": "HELD"}


@app.post("/reservations/{rid}/confirm")
def confirm(rid: int):
    return {"reservation_id": rid, "status": "CONFIRMED"}


@app.post("/reservations/{rid}/release")
def release(rid: int):
    return {"reservation_id": rid, "status": "RELEASED"}


@app.get("/stock")
def stock():
    return {"tiers": [{"id": 1, "total": 1000, "reserved": 0, "sold": 0, "available": 1000}]}