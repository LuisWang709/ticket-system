"""Iteration 1: a durable mock payment API; no real money or payment provider."""
import os
import random
import time
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import CollectorRegistry, Counter
from prometheus_fastapi_instrumentator import Instrumentator


SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    order_id TEXT PRIMARY KEY,
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PaymentRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=128)
    amount_cents: int = Field(ge=1, le=9223372036854775807)


class PaymentResponse(BaseModel):
    status: str


class PaymentConfig(BaseModel):
    delay_ms: int = Field(default=0, ge=0, le=10000)
    fail_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class ConfigUpdate(BaseModel):
    delay_ms: int | None = Field(default=None, ge=0, le=10000)
    fail_rate: float | None = Field(default=None, ge=0.0, le=1.0)


def create_app(database_url=None, config=None):
    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/payments"
    )

    @asynccontextmanager
    async def lifespan(app):
        with psycopg.connect(database_url) as conn:
            conn.execute(SCHEMA)
        yield

    app = FastAPI(title="Mock payment service - iteration 1", lifespan=lifespan)
    app.state.config = config or PaymentConfig(
        delay_ms=int(os.getenv("DELAY_MS", "0")),
        fail_rate=float(os.getenv("FAIL_RATE", "0")),
    )
    registry = CollectorRegistry()
    payments_total = Counter("payments_total", "New mock payment outcomes", ["result"], registry=registry)
    Instrumentator(registry=registry).instrument(app).expose(app)

    @app.get("/health")
    def health():
        with psycopg.connect(database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "service": "payment"}

    @app.get("/admin/config", response_model=PaymentConfig)
    def get_config():
        return app.state.config

    @app.post("/admin/config", response_model=PaymentConfig)
    def set_config(body: ConfigUpdate):
        app.state.config = PaymentConfig(**{
            **app.state.config.model_dump(), **body.model_dump(exclude_none=True),
        })
        return app.state.config

    @app.post("/payments", response_model=PaymentResponse)
    def pay(body: PaymentRequest):
        try:
            with psycopg.connect(database_url, row_factory=dict_row) as conn:
                conn.execute("SET LOCAL lock_timeout = '5s'")
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (body.order_id,))
                existing = conn.execute("SELECT * FROM payments WHERE order_id = %s", (body.order_id,)).fetchone()
                if existing:
                    if existing["amount_cents"] != body.amount_cents:
                        raise HTTPException(409, "This order_id already has a payment with a different amount")
                    return {"status": existing["status"]}
                settings = app.state.config
                time.sleep(settings.delay_ms / 1000)
                result = "FAILED" if random.random() < settings.fail_rate else "SUCCEEDED"
                conn.execute(
                    "INSERT INTO payments (order_id, amount_cents, status) VALUES (%s, %s, %s)",
                    (body.order_id, body.amount_cents, result),
                )
            payments_total.labels(result=result).inc()
            return {"status": result}
        except psycopg.errors.LockNotAvailable:
            raise HTTPException(503, "Payment is processing; retry with the same order_id and amount")

    return app


app = create_app()
