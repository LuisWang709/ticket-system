import os
import psycopg2
from psycopg2.extras import RealDictCursor
import redis


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/inventory")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# atomic update
def reserve_by_atomic_update(tier_id: str, quantity: int) -> bool:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cursor:
            query = """
                UPDATE ticket_inventory
                SET available_tickets = available_tickets - %s,
                    reserved_tickets = reserved_tickets + %s
                WHERE tier_id = %s AND available_tickets >= %s;
            """
            cursor.execute(query, (quantity, quantity, tier_id, quantity))
            success = cursor.rowcount > 0
            conn.commit()
            return success
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# rowlock
def reserve_by_row_locking(tier_id: str, quantity: int) -> bool:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT available_tickets FROM ticket_inventory WHERE tier_id = %s FOR UPDATE;", (tier_id,))
            record = cursor.fetchone()
            if not record or record['available_tickets'] < quantity:
                conn.commit()
                return False

            cursor.execute("""
                UPDATE ticket_inventory
                SET available_tickets = available_tickets - %s,
                    reserved_tickets = reserved_tickets + %s
                WHERE tier_id = %s;
            """, (quantity, quantity, tier_id))
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# Redis Pre-deduction
LUA_DEDUCT_SCRIPT = """
local key = KEYS[1]
local quantity = tonumber(ARGV[1])
local available = tonumber(redis.call('get', key) or 0)
if available >= quantity then
    redis.call('decrby', key, quantity)
    return 1
else
    return 0
end
"""

def reserve_by_redis(tier_id: str, quantity: int) -> bool:
    redis_key = f"inventory:{tier_id}:available"
    res = redis_client.eval(LUA_DEDUCT_SCRIPT, 1, redis_key, quantity)
    if res == 1:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ticket_inventory
                    SET available_tickets = available_tickets - %s,
                        reserved_tickets = reserved_tickets + %s
                    WHERE tier_id = %s;
                """, (quantity, quantity, tier_id))
                conn.commit()
            return True
        except Exception:
            conn.rollback()
            redis_client.incrby(redis_key, quantity)
            return False
        finally:
            conn.close()
    return False