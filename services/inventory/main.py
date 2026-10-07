import os
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor

import strategies

# ----------------- Configuration Reading -----------------
HOLD_SECONDS = int(os.getenv("HOLD_SECONDS"))
DEFAULT_MODE = os.getenv("INVENTORY_MODE", "atomic")

app = FastAPI(title="Inventory Service - Full Contract")

# health check
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "inventory"}

@app.get("/metrics")
def metrics():
    return "# HELP inventory_up Service health status\n# TYPE inventory_up gauge\ninventory_up 1\n"

# ----------------- request -----------------
class ReserveRequest(BaseModel):
    order_id: str
    tier_id: str
    quantity: int = 1
    strategy: str = None

class ConfirmRequest(BaseModel):
    reservation_id: str

class ReleaseRequest(BaseModel):
    reservation_id: str


# ----------------- reserve -----------------
@app.post("/inventory/reserve")
def reserve_ticket(req: ReserveRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than 0")

    # Order History Check
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT reservation_id, status, expires_at 
                FROM reservations 
                WHERE order_id = %s;
            """, (req.order_id,))
            existing = cur.fetchone()

            if existing:
                now_utc = datetime.now(timezone.utc)
                # An idempotent retry is valid only when the state is ACTIVE and it has not yet expired.
                if existing["status"] == "ACTIVE" and existing["expires_at"] > now_utc:
                    return {
                        "success": True,
                        "reservation_id": existing["reservation_id"],
                        "status": "ACTIVE",
                        "expires_at": existing["expires_at"].isoformat(),
                        "message": "Idempotent duplicate: active reservation returned"
                    }
                # Any request to re-lock tickets will be rejected if the session has timed out or the tickets have already been released or sold.
                else:
                    status_desc = "EXPIRED" if (
                                existing["status"] == "ACTIVE" and existing["expires_at"] <= now_utc) else existing[
                        "status"]
                    return {
                        "success": False,
                        "reservation_id": existing["reservation_id"],
                        "status": status_desc,
                        "message": f"Order already processed or expired (current status: {status_desc})"
                    }
    finally:
        conn.close()

    # First-order discount
    mode = req.strategy or DEFAULT_MODE
    if mode == "atomic":
        success = strategies.reserve_by_atomic_update(req.tier_id, req.quantity)
    elif mode in ["rowlock", "row_lock"]:
        success = strategies.reserve_by_row_locking(req.tier_id, req.quantity)
    elif mode == "redis":
        success = strategies.reserve_by_redis(req.tier_id, req.quantity)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {mode}")

    if not success:
        return {"success": False, "message": "Sold out or insufficient inventory"}

    # 3. Initial reservation successful: ACTIVE transaction record generated.
    res_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=HOLD_SECONDS)

    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reservations (reservation_id, order_id, tier_id, quantity, status, expires_at)
                VALUES (%s, %s, %s, %s, 'ACTIVE', %s);
            """, (res_id, req.order_id, req.tier_id, req.quantity, expires_at))
            conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "reservation_id": res_id,
        "status": "ACTIVE",
        "expires_at": expires_at.isoformat()
    }


# ----------------- confirm -----------------
@app.post("/inventory/confirm")
def confirm_reservation(req: ConfirmRequest):
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 核心修复：只有状态为 ACTIVE 且当前时间仍在有效窗口期内的记录才允许确认！
            cur.execute("""
                UPDATE reservations 
                SET status = 'CONFIRMED'
                WHERE reservation_id = %s 
                  AND status = 'ACTIVE' 
                  AND expires_at > CURRENT_TIMESTAMP
                RETURNING tier_id, quantity;
            """, (req.reservation_id,))
            res = cur.fetchone()

            # If the update fails, it indicates that: 1. It does not exist; 2. It has already been released; or 3. The expires_at time has passed.
            if not res:
                # Check if the failure was caused by a timeout; if a timeout occurred and the status is still ACTIVE, mark it as RELEASED and roll back the inventory.
                cur.execute("""
                    SELECT tier_id, quantity, status, expires_at 
                    FROM reservations 
                    WHERE reservation_id = %s;
                """, (req.reservation_id,))
                record = cur.fetchone()

                if record and record['status'] == 'ACTIVE' and record['expires_at'] <= datetime.now(timezone.utc):
                    # Status timed out; proceeding with immediate release.
                    cur.execute("UPDATE reservations SET status = 'RELEASED' WHERE reservation_id = %s;", (req.reservation_id,))
                    cur.execute("""
                        UPDATE ticket_inventory
                        SET available_tickets = available_tickets + %s, reserved_tickets = reserved_tickets - %s
                        WHERE tier_id = %s;
                    """, (record['quantity'], record['quantity'], record['tier_id']))
                    conn.commit()
                    strategies.redis_client.incrby(f"inventory:{record['tier_id']}:available", record['quantity'])

                    return {
                        "success": False,
                        "message": "Reservation hold has expired. Inventory released."
                    }

                return {
                    "success": False,
                    "message": "Reservation not in valid ACTIVE status or already processed"
                }

            # Standard ticket issuance confirmation: Update general ledger
            cur.execute("""
                UPDATE ticket_inventory
                SET reserved_tickets = reserved_tickets - %s, confirmed_tickets = confirmed_tickets + %s
                WHERE tier_id = %s;
            """, (res['quantity'], res['quantity'], res['tier_id']))
            conn.commit()
            return {"success": True, "status": "CONFIRMED"}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# ----------------- release -----------------
@app.post("/inventory/release")
def release_reservation(req: ReleaseRequest):
    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE reservations SET status = 'RELEASED'
                WHERE reservation_id = %s AND status = 'ACTIVE'
                RETURNING tier_id, quantity;
            """, (req.reservation_id,))
            res = cur.fetchone()
            if not res:
                return {"success": False, "message": "Reservation already released or confirmed"}

            cur.execute("""
                UPDATE ticket_inventory
                SET available_tickets = available_tickets + %s, reserved_tickets = reserved_tickets - %s
                WHERE tier_id = %s;
            """, (res['quantity'], res['quantity'], res['tier_id']))
            conn.commit()
            strategies.redis_client.incrby(f"inventory:{res['tier_id']}:available", res['quantity'])
            return {"success": True, "status": "RELEASED"}
    finally:
        conn.close()


# ----------------- Sweep Expired -----------------
@app.post("/inventory/sweep-expired")
def sweep_expired():

    now = datetime.now(timezone.utc)
    conn = psycopg2.connect(strategies.DATABASE_URL)
    released_count = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT reservation_id, tier_id, quantity FROM reservations
                WHERE status = 'ACTIVE' AND expires_at < %s
                FOR UPDATE SKIP LOCKED;
            """, (now,))
            rows = cur.fetchall()

            for item in rows:
                cur.execute("UPDATE reservations SET status = 'RELEASED' WHERE reservation_id = %s;", (item['reservation_id'],))
                cur.execute("""
                    UPDATE ticket_inventory
                    SET available_tickets = available_tickets + %s, reserved_tickets = reserved_tickets - %s
                    WHERE tier_id = %s;
                """, (item['quantity'], item['quantity'], item['tier_id']))
                strategies.redis_client.incrby(f"inventory:{item['tier_id']}:available", item['quantity'])
                released_count += 1
            conn.commit()
    finally:
        conn.close()
    return {"released_reservations": released_count}


# ----------------- Get Tier Status -----------------
@app.get("/inventory/status/{tier_id}")
def get_inventory_status(tier_id: str):

    conn = psycopg2.connect(strategies.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ticket_inventory WHERE tier_id = %s;", (tier_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Tier '{tier_id}' not found")
            return row
    finally:
        conn.close()