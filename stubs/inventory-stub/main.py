"""Fake inventory: always says yes. Lets B/D work before A is ready.

The response shapes here must match the real inventory service exactly,
otherwise the order service rejects the response with 502. See docs/contract.md.
"""
import uuid

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "stub", "service": "inventory-stub"}


@app.post("/reservations")
def reserve(body: dict):
    # Order service checks: status == "ACTIVE" and reservation_id is a non-empty string.
    return {"reservation_id": str(uuid.uuid4()), "status": "ACTIVE"}


@app.post("/reservations/{rid}/confirm")
def confirm(rid: str):
    return {"status": "CONFIRMED"}


@app.post("/reservations/{rid}/release")
def release(rid: str):
    return {"status": "RELEASED"}


@app.get("/stock")
def stock():
    # Same shape as the real service: a bare list, keyed by tier_id.
    return [{"tier_id": "1", "total": 1000, "reserved": 0, "sold": 0, "available": 1000}]
