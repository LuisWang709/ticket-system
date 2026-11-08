"""Real PostgreSQL tests, controlled inventory responses, and live Compose smoke tests."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from services.order.app.main import create_app as order_app
from services.payment.app.main import create_app as payment_app, PaymentConfig

LIVE_URLS = ("LIVE_ORDER_URL", "LIVE_PAYMENT_URL")
needs_live_stack = pytest.mark.skipif(
    any(not os.environ.get(name) for name in LIVE_URLS),
    reason="Needs the running stack: docker compose --profile test run --rm tests",
)


@pytest.fixture(scope="session")
def database_url():
    # Create and remove ONLY our uniquely named test database.
    admin_url = os.environ["TEST_ADMIN_DATABASE_URL"]
    name = "member_b_test_" + uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo(admin_url, dbname=name)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def request_body():
    return {"user_id": "student-demo", "tier_id": "1", "qty": 1, "amount_cents": 25000}


def headers():
    return {"Idempotency-Key": str(uuid4())}


def test_order_create_query_replay_and_changed_request(database_url):
    calls = []
    def inventory(request):
        calls.append(request)
        return httpx.Response(200, json={"reservation_id": str(uuid4()), "status": "ACTIVE"})
    app = order_app(database_url, transport=httpx.MockTransport(inventory))
    key = headers()
    with TestClient(app) as client:
        first = client.post("/orders", json=request_body(), headers=key)
        assert first.status_code == 200
        assert first.json()["status"] == "PENDING"
        assert client.get("/orders/" + first.json()["order_id"]).json() == first.json()
        assert client.post("/orders", json=request_body(), headers=key).json() == first.json()
        assert len(calls) == 1
        assert client.post("/orders", json={**request_body(), "qty": 2}, headers=key).status_code == 409
        assert client.get("/orders/" + str(uuid4())).status_code == 404
        assert "orders_total" in client.get("/metrics").text
    # Recreating the app simulates a process restart; the order stays in PostgreSQL.
    with TestClient(order_app(database_url, transport=httpx.MockTransport(inventory))) as client:
        assert client.post("/orders", json=request_body(), headers=key).json() == first.json()
    assert len(calls) == 1


def test_order_validation_never_reserves(database_url):
    def unexpected(_):
        pytest.fail("Invalid requests must not reach inventory")
    with TestClient(order_app(database_url, transport=httpx.MockTransport(unexpected))) as client:
        assert client.post("/orders", json=request_body()).status_code == 422
        assert client.post("/orders", json=request_body(), headers={"Idempotency-Key": "invalid"}).status_code == 422
        assert client.post("/orders", json={**request_body(), "qty": 0}, headers=headers()).status_code == 422


def test_order_sold_out_is_durable_rejection(database_url):
    with TestClient(order_app(database_url, transport=httpx.MockTransport(
        lambda _: httpx.Response(409, json={"detail": "Sold out"})
    ))) as client:
        result = client.post("/orders", json=request_body(), headers=headers())
        assert result.status_code == 200
        assert result.json()["status"] == "REJECTED"
        assert result.json()["reservation_id"] is None
        assert client.get("/orders/" + result.json()["order_id"]).json() == result.json()


def test_timeout_retry_reuses_order_id(database_url):
    ids = []
    def inventory(request):
        import json
        ids.append(json.loads(request.content)["order_id"])
        if len(ids) == 1:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={"reservation_id": str(uuid4()), "status": "ACTIVE"})
    with TestClient(order_app(database_url, transport=httpx.MockTransport(inventory))) as client:
        key = headers()
        failed = client.post("/orders", json=request_body(), headers=key)
        assert failed.status_code == 503
        order_id = failed.json()["detail"]["order_id"]
        assert client.get("/orders/" + order_id).json()["status"] == "NEW"
        retried = client.post("/orders", json=request_body(), headers=key)
        assert retried.json()["status"] == "PENDING"
        assert ids == [order_id, order_id]


def test_malformed_inventory_response_does_not_mark_pending(database_url):
    with TestClient(order_app(database_url, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "ACTIVE"})
    ))) as client:
        result = client.post("/orders", json=request_body(), headers=headers())
        assert result.status_code == 502
        order_id = result.json()["detail"]["order_id"]
        assert client.get("/orders/" + order_id).json()["status"] == "NEW"


def test_simultaneous_duplicate_orders_only_reserve_once(database_url):
    calls = []
    def inventory(request):
        calls.append(request)
        time.sleep(0.1)
        return httpx.Response(200, json={"reservation_id": str(uuid4()), "status": "ACTIVE"})
    with TestClient(order_app(database_url, transport=httpx.MockTransport(inventory))) as client:
        key = headers()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.post("/orders", json=request_body(), headers=key), range(4)))
        assert all(r.status_code == 200 for r in results)
        assert len({r.json()["order_id"] for r in results}) == 1
        assert len(calls) == 1


def test_payment_result_persists_and_rejects_changed_amount(database_url):
    body = {"order_id": str(uuid4()), "amount_cents": 25000}
    with TestClient(payment_app(database_url)) as client:
        assert client.post("/payments", json=body).json() == {"status": "SUCCEEDED"}
        assert client.post("/admin/config", json={"fail_rate": 1}).status_code == 200
        assert client.post("/payments", json=body).json() == {"status": "SUCCEEDED"}
        assert client.post("/payments", json={**body, "amount_cents": 100}).status_code == 409
        assert client.post("/payments", json={**body, "order_id": str(uuid4())}).json() == {"status": "FAILED"}
        assert "payments_total" in client.get("/metrics").text
        assert client.post("/admin/config", json={"fail_rate": 2}).status_code == 422
        assert client.post("/payments", json={**body, "amount_cents": 0}).status_code == 422
    with TestClient(payment_app(database_url, config=PaymentConfig(fail_rate=1))) as client:
        assert client.post("/payments", json=body).json() == {"status": "SUCCEEDED"}


def test_simultaneous_duplicate_payments_write_one_record(database_url):
    body = {"order_id": str(uuid4()), "amount_cents": 25000}
    with TestClient(payment_app(database_url, config=PaymentConfig(delay_ms=100))) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.post("/payments", json=body), range(4)))
        assert all(r.status_code == 200 and r.json()["status"] == "SUCCEEDED" for r in results)
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM payments WHERE order_id=%s", (body["order_id"],)).fetchone()[0] == 1


@needs_live_stack
def test_live_order_with_real_inventory_and_separate_payment():
    order_url = os.environ["LIVE_ORDER_URL"]
    payment_url = os.environ["LIVE_PAYMENT_URL"]
    with httpx.Client(timeout=10) as client:
        key = headers()
        order = client.post(order_url + "/orders", json=request_body(), headers=key)
        assert order.status_code == 200, order.text
        assert order.json()["status"] == "PENDING", order.text
        assert client.post(order_url + "/orders", json=request_body(), headers=key).json() == order.json()
        payment = client.post(payment_url + "/payments", json={"order_id": order.json()["order_id"], "amount_cents": 25000})
        assert payment.status_code == 200
        assert payment.json()["status"] in ["SUCCEEDED", "FAILED"]
        # This intentionally demonstrates the iteration-1 boundary: payment is not linked yet.
        assert client.get(order_url + "/orders/" + order.json()["order_id"]).json()["status"] == "PENDING"
