"""Catalog service (owner: Xiang Wang): read-only list of
 concerts and ticket tiers (synthetic data)."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/catalog")
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INT PRIMARY KEY, name TEXT NOT NULL, venue TEXT NOT NULL, starts_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS tiers (
    id INT PRIMARY KEY, event_id INT NOT NULL REFERENCES events(id), name TEXT NOT NULL, price_cents INT NOT NULL);
INSERT INTO events VALUES (1, 'Synthetic Stars Live', 'Demo Arena, Dublin', '2026-12-01 19:30+00') ON CONFLICT DO NOTHING;
INSERT INTO tiers VALUES (1, 1, 'VIP', 25000), (2, 1, 'Standard', 9000), (3, 1, 'Balcony', 4500) ON CONFLICT DO NOTHING;
"""


@asynccontextmanager
async def lifespan(app):
    pool.open(wait=True, timeout=60)
    with pool.connection() as conn:
        conn.execute(SCHEMA)
    yield
    pool.close()


app = FastAPI(title="catalog", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/events")
def events():
    with pool.connection() as conn:
        return {"events": conn.execute("SELECT * FROM events ORDER BY id").fetchall()}


@app.get("/events/{event_id}")
def event(event_id: int):
    with pool.connection() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=%s", (event_id,)).fetchone()
        if ev is None:
            raise HTTPException(404, "not_found")
        ev["tiers"] = conn.execute("SELECT id, name, price_cents FROM tiers WHERE event_id=%s ORDER BY id",
                                   (event_id,)).fetchall()
    return ev