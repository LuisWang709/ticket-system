"""Iteration 1: persist an order and reserve inventory; payment is not yet linked."""
import hashlib
import json
import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import CollectorRegistry, Counter
from prometheus_fastapi_instrumentator import Instrumentator


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tier_id TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    status TEXT NOT NULL CHECK (status IN ('NEW', 'PENDING', 'REJECTED')),
    reason TEXT,
    reservation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class OrderRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tier_id: str = Field(min_length=1, max_length=50)
    qty: int = Field(ge=1, le=2147483647)
    amount_cents: int = Field(ge=1, le=9223372036854775807)
    # Keep the team's request format. Risk assessment is a later iteration.
    clicks_last_1s: int = Field(default=0, ge=0)
    requests_last_10s: int = Field(default=0, ge=0)
    account_age_days: int = Field(default=0, ge=0)
    ms_page_to_click: int = Field(default=0, ge=0)
    accounts_on_device: int = Field(default=1, ge=0)


class OrderResponse(BaseModel):
    order_id: str
    status: str
    reason: str | None
    reservation_id: str | None


def public_order(row):
    return {key: row[key] for key in OrderResponse.model_fields}


def create_app(database_url=None, inventory_url=None, transport=None):
    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/orders"
    )
    inventory_url = inventory_url or os.getenv("INVENTORY_URL", "http://inventory:8000")

    @asynccontextmanager
    async def lifespan(app):
        with psycopg.connect(database_url) as conn:
            conn.execute(SCHEMA)
        with httpx.Client(base_url=inventory_url, timeout=3.0, transport=transport) as client:
            app.state.inventory = client
            yield

    app = FastAPI(title="Order service - iteration 1", lifespan=lifespan)
    registry = CollectorRegistry()
    orders_total = Counter("orders_total", "Orders entering a state in this process", ["status"], registry=registry)
    Instrumentator(registry=registry).instrument(app).expose(app)

    @app.get("/health")
    def health():
        with psycopg.connect(database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "service": "order"}

    @app.get("/orders/{order_id}", response_model=OrderResponse)
    def get_order(order_id: str):
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found")
        return public_order(row)

    @app.post("/orders", response_model=OrderResponse)
    def create_order(body: OrderRequest, idempotency_key: Annotated[UUID, Header()]):
        key = str(idempotency_key)
        request_hash = hashlib.sha256(
            json.dumps(body.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        try:
            # A session lock serializes matching keys across service processes.
            # Autocommit persists NEW before the HTTP call, so a retry reuses its ID.
            # Closing this connection releases the lock, including on exceptions.
            with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
                conn.execute("SET lock_timeout = '5s'")
                conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
                row = conn.execute("SELECT * FROM orders WHERE idempotency_key = %s", (key,)).fetchone()
                if row:
                    if row["request_hash"] != request_hash:
                        raise HTTPException(409, "Idempotency-Key already used with a different request")
                    if row["status"] != "NEW":
                        return public_order(row)
                else:
                    row = conn.execute(
                        """INSERT INTO orders
                        (order_id, idempotency_key, request_hash, user_id, tier_id, qty, amount_cents, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'NEW') RETURNING *""",
                        (str(uuid4()), key, request_hash, body.user_id, body.tier_id, body.qty, body.amount_cents),
                    ).fetchone()
                    orders_total.labels(status="NEW").inc()

                try:
                    response = app.state.inventory.post("/reservations", json={
                        "order_id": row["order_id"], "tier_id": body.tier_id, "qty": body.qty,
                    })
                except httpx.RequestError:
                    # Timeout does not prove that inventory failed. Keep NEW for retry.
                    raise HTTPException(503, {"order_id": row["order_id"], "reason": "INVENTORY_UNAVAILABLE", "retry_with_same_key": True})

                reservation_id = None
                if response.status_code == 409:
                    next_status, reason = "REJECTED", "INVENTORY_REJECTED"
                elif response.status_code in (200, 201):
                    try:
                        payload = response.json()
                        reservation_id = payload["reservation_id"]
                        if payload["status"] != "ACTIVE" or not isinstance(reservation_id, str) or not reservation_id:
                            raise ValueError("Invalid reservation response")
                    except (ValueError, KeyError, TypeError):
                        raise HTTPException(502, {"order_id": row["order_id"], "reason": "INVALID_INVENTORY_RESPONSE"})
                    next_status, reason = "PENDING", None
                else:
                    raise HTTPException(503, {"order_id": row["order_id"], "reason": "INVENTORY_UNAVAILABLE", "retry_with_same_key": True})

                row = conn.execute(
                    """UPDATE orders SET status = %s, reason = %s, reservation_id = %s
                    WHERE order_id = %s RETURNING *""",
                    (next_status, reason, reservation_id, row["order_id"]),
                ).fetchone()
                orders_total.labels(status=next_status).inc()
                return public_order(row)
        except psycopg.errors.LockNotAvailable:
            raise HTTPException(503, "Request with this key is still processing; retry with the same key")

    return app


app = create_app()
