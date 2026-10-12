import os
import json
import uuid
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, status, Response
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pika
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

import strategies

HOLD_SECONDS = int(os.getenv("HOLD_SECONDS", "30"))
CURRENT_MODE = os.getenv("INVENTORY_MODE", "atomic")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

app = FastAPI(title="Inventory Service")

# 监控指标
METRIC_RESERVATIONS_TOTAL = Counter("inventory_reservations_total", "Reservations", ["result", "mode"])
METRIC_RESERVE_SECONDS = Histogram("inventory_reserve_seconds", "Latency", ["mode"])
METRIC_ACTIVE_RESERVATIONS = Gauge("inventory_active_reservations", "Active holds")
METRIC_REDIS_FALLBACK_TOTAL = Counter("inventory_redis_fallback_total", "Fallbacks")
METRIC_OUTBOX_PENDING = Gauge("outbox_pending", "Pending outbox")

@app.get("/health")
def health():
    return {"status": "ok", "service": "inventory"}

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

class ReservationRequest(BaseModel):
    order_id: str
    tier_id: str
    qty: int

class ModeRequest(BaseModel):
    mode: str

class ResetRequest(BaseModel):
    total: Optional[int] = 100

def publish_reservation_expired(res_id: str, order_id: str):
    try:
        params = pika.URLParameters(RABBITMQ_URL)
        conn = pika.BlockingConnection(params)
        ch = conn.channel()
        ch.exchange_declare(exchange="events", exchange_type="topic", durable=True)
        payload = json.dumps({"order_id": order_id, "reservation_id": res_id})
        ch.basic_publish(
            exchange="events",
            routing_key="reservation.expired",
            body=payload.encode("utf-8"),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json", message_id=f"inventory-{res_id[:8]}")
        )
        conn.close()
    except Exception as e:
        print(f"[WARN] RabbitMQ event failed: {e}")

# 1. 预留库存
@app.post("/reservations")
def create_reservation(req: ReservationRequest):
    if req.qty <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="qty must be > 0")

    global CURRENT_MODE
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 【重复检测 1】：幂等防重检查
            cur.execute("SELECT reservation_id, status, expires_at FROM reservations WHERE order_id = %s;", (req.order_id,))
            existing = cur.fetchone()

            if existing:
                now_utc = datetime.now(timezone.utc)
                # 若之前已成功锁票且尚未过期，幂等返回 200，绝不扣第2次库存
                if existing["status"] == "ACTIVE" and existing["expires_at"] > now_utc:
                    return {
                        "reservation_id": existing["reservation_id"],
                        "status": "ACTIVE"
                    }
                else:
                    # 订单若已处于终态或已超时，拒绝重新占票，抛出 409
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order already processed or expired")
    finally:
        conn.close()

    # 执行并发扣减
    start = time.time()
    success = False
    if CURRENT_MODE == "atomic":
        success = strategies.reserve_by_atomic_update(req.tier_id, req.qty)
    elif CURRENT_MODE == "rowlock":
        success = strategies.reserve_by_row_locking(req.tier_id, req.qty)
    elif CURRENT_MODE == "redis":
        success = strategies.reserve_by_redis(req.tier_id, req.qty)
        if not success:
            METRIC_REDIS_FALLBACK_TOTAL.inc()

    METRIC_RESERVE_SECONDS.labels(mode=CURRENT_MODE).observe(time.time() - start)

    # 档位不存在或库存不足，必须抛出 409 Conflict
    if not success:
        METRIC_RESERVATIONS_TOTAL.labels(result="sold_out", mode=CURRENT_MODE).inc()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sold out")

    METRIC_RESERVATIONS_TOTAL.labels(result="ok", mode=CURRENT_MODE).inc()

    res_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=HOLD_SECONDS)

    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reservations (reservation_id, order_id, tier_id, quantity, status, expires_at)
                VALUES (%s, %s, %s, %s, 'ACTIVE', %s);
            """, (res_id, req.order_id, req.tier_id, req.qty, expires_at))
            conn.commit()
    finally:
        conn.close()

    return {
        "reservation_id": res_id,
        "status": "ACTIVE"
    }

# 2. 确认库存
@app.post("/reservations/{reservation_id}/confirm")
def confirm_reservation(reservation_id: str):
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, expires_at, quantity, tier_id FROM reservations WHERE reservation_id = %s;", (reservation_id,))
            record = cur.fetchone()

            # 【状态检测 1】：提交不存在的 reservation_id，抛出 404
            if not record:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")

            # 【重复检测 2】：如果已经被确认出票，重复确认直接幂等返回 200
            if record["status"] == "CONFIRMED":
                return {"status": "CONFIRMED"}

            # 【状态检测 2】：已过期或已被释放，抛出 409
            now_utc = datetime.now(timezone.utc)
            if record["status"] != "ACTIVE" or record["expires_at"] <= now_utc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reservation hold has expired or released")

            cur.execute("UPDATE reservations SET status = 'CONFIRMED' WHERE reservation_id = %s;", (reservation_id,))
            cur.execute("""
                UPDATE ticket_inventory
                SET reserved_tickets = reserved_tickets - %s, confirmed_tickets = confirmed_tickets + %s
                WHERE tier_id = %s;
            """, (record["quantity"], record["quantity"], record["tier_id"]))
            conn.commit()

            return {"status": "CONFIRMED"}
    finally:
        conn.close()

# 3. 释放库存
@app.post("/reservations/{reservation_id}/release")
def release_reservation(reservation_id: str):
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, quantity, tier_id FROM reservations WHERE reservation_id = %s;", (reservation_id,))
            record = cur.fetchone()

            # 【状态检测 1】：提交不存在的流水，抛出 404
            if not record:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")

            # 【重复检测 3】：若已释放过，重复释放幂等返回 200
            if record["status"] == "RELEASED":
                return {"status": "RELEASED"}

            if record["status"] != "ACTIVE":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot release reservation in current status")

            cur.execute("UPDATE reservations SET status = 'RELEASED' WHERE reservation_id = %s;", (reservation_id,))
            cur.execute("""
                UPDATE ticket_inventory
                SET available_tickets = available_tickets + %s, reserved_tickets = reserved_tickets - %s
                WHERE tier_id = %s;
            """, (record["quantity"], record["quantity"], record["tier_id"]))
            conn.commit()

            strategies.redis_client.incrby(f"inventory:{record['tier_id']}:available", record["quantity"])
            return {"status": "RELEASED"}
    finally:
        conn.close()

# 4. 全量库存查询
@app.get("/stock")
def get_all_stock():
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT tier_id, total_capacity, reserved_tickets, confirmed_tickets, available_tickets FROM ticket_inventory ORDER BY tier_id;")
            rows = cur.fetchall()
            return [
                {
                    "tier_id": row["tier_id"],
                    "total": row["total_capacity"],
                    "reserved": row["reserved_tickets"],
                    "sold": row["confirmed_tickets"],
                    "available": row["available_tickets"]
                }
                for row in rows
            ]
    finally:
        conn.close()

# 5. 管理端点
@app.get("/admin/mode")
def get_mode():
    return {"mode": CURRENT_MODE, "modes": ["atomic", "rowlock", "redis"]}

@app.post("/admin/mode")
def set_mode(req: ModeRequest):
    global CURRENT_MODE
    if req.mode not in ["atomic", "rowlock", "redis"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unrecognized mode")
    if req.mode == "redis":
        try:
            strategies.redis_client.ping()
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unreachable")
    CURRENT_MODE = req.mode
    return {"mode": CURRENT_MODE}

@app.post("/admin/reset")
def reset_inventory(req: ResetRequest):
    total = req.total if req.total is not None else 100
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reservations;")
            cur.execute("""
                UPDATE ticket_inventory
                SET total_capacity = %s, available_tickets = %s, reserved_tickets = 0, confirmed_tickets = 0
                WHERE tier_id = '1';
            """, (total, total))
            conn.commit()
    finally:
        conn.close()
    try:
        strategies.redis_client.set("inventory:1:available", total)
    except Exception:
        pass
    return {"status": "reset_completed", "tier_1_total": total}

@app.get("/admin/redis-check")
def redis_check():
    conn = psycopg2.connect(strategies.DATABASE_URL)
    result = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT tier_id, available_tickets FROM ticket_inventory ORDER BY tier_id;")
            for r in cur.fetchall():
                val = strategies.redis_client.get(f"inventory:{r['tier_id']}:available")
                cnt = int(val) if val is not None else -1
                result.append({"tier_id": r["tier_id"], "database_available": r["available_tickets"], "redis_counter": cnt, "in_sync": (r["available_tickets"] == cnt)})
        return result
    finally:
        conn.close()

# 6. 超时清扫
@app.post("/inventory/sweep-expired")
def sweep_expired():
    now = datetime.now(timezone.utc)
    conn = psycopg2.connect(strategies.DATABASE_URL)
    released_list = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT reservation_id, order_id, tier_id, quantity FROM reservations WHERE status = 'ACTIVE' AND expires_at < %s FOR UPDATE SKIP LOCKED;", (now,))
            rows = cur.fetchall()
            for item in rows:
                cur.execute("UPDATE reservations SET status = 'RELEASED' WHERE reservation_id = %s;", (item['reservation_id'],))
                cur.execute("UPDATE ticket_inventory SET available_tickets = available_tickets + %s, reserved_tickets = reserved_tickets - %s WHERE tier_id = %s;", (item['quantity'], item['quantity'], item['tier_id']))
                strategies.redis_client.incrby(f"inventory:{item['tier_id']}:available", item['quantity'])
                released_list.append(item)
            conn.commit()
    finally:
        conn.close()

    for item in released_list:
        publish_reservation_expired(item["reservation_id"], item["order_id"])
    return {"released_reservations": len(released_list)}