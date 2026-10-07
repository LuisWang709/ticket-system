# AI Interaction & Critical Reflection Log

- **Author / Contributor**: Tianyue Li (Member A - Inventory Service Lead)
- **Course**: COMP41720 Distributed Systems (2026-27)
- **Role / Area**: `services/inventory/`, `scripts/reconcile.sh`, Database Concurrency & Auditing

---

### Entry 1: Core Inventory Deduction Concurrency Strategies
- **Date**: October 6, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "Design three distinct high-concurrency ticket reservation algorithms in Python for PostgreSQL and Redis: atomic conditional updates, pessimistic row-level locking, and a Redis Lua pre-decrement script. Ensure zero overselling under race conditions."
- **What was accepted**:
  - The atomic SQL statement utilizing PostgreSQL's implicit row-level lock via `UPDATE ... SET available_tickets = available_tickets - q WHERE available_tickets >= q;` checking `cursor.rowcount > 0`.
  - The atomic Redis Lua script logic evaluating available capacity and executing `decrby` atomically.
- **What was modified / rejected**:
  - **Modified**: The initial AI code for the Redis strategy merely decremented the Redis counter and left PostgreSQL updates completely unhandled, treating Redis as an ephemeral cache. I modified the implementation to perform a synchronized write-back to PostgreSQL with a compensating `incrby` rollback in case of database transaction rollback, preventing data divergence between memory and disk.
- **Architectural Reflection & Critique**:
  - The AI initially prioritized raw throughput over cross-datastore consistency. In distributed ticket sales, an unbacked Redis decrement creates split-brain states if the process crashes before persistent ledger commitment. Enforcing explicit compensation ensures strong durability.

---

### Entry 2: Reservation Lifecycle State Machine and Sweeping Mechanism
- **Date**: October 6, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "Implement the ticket reservation lifecycle (ACTIVE -> CONFIRMED / RELEASED) with a 10-minute lock window, plus an automated sweeper for expired holds."
- **What was accepted**:
  - The tripartite state machine definition and timestamp calculation using UTC timezone (`expires_at = datetime.now(timezone.utc) + timedelta(seconds=HOLD_SECONDS)`).
  - The conditional status transitions ensuring only `ACTIVE` reservations can transition to `CONFIRMED` or `RELEASED`.
- **What was modified / rejected**:
  - **Rejected**: The AI proposed embedding an in-process background scheduler (using `APScheduler` or a continuous `asyncio` loop) directly within the FastAPI worker process to sweep expired rows every 5 seconds.
  - **Why rejected**: Running stateful background cron jobs inside a horizontally scaled microservice container causes concurrency contention, database locking spikes, and violates the Twelve-Factor App model. I replaced this with an idempotent HTTP endpoint (`POST /inventory/sweep-expired`) using `FOR UPDATE SKIP LOCKED`, allowing sweeping tasks to be triggered by an external scheduler without coupling scheduling state to service replicas.

---

### Entry 3: Idempotency Protection Against Network Retries
- **Date**: October 7, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "Can identical order_id values be repeatedly submitted to /inventory/reserve? Does it matter in high-concurrency ticket sales?"
- **What was accepted**:
  - The AI's verification that in distributed networks (where timeouts do not imply non-execution), duplicate requests are inevitable due to upstream retry policies (at-least-once delivery semantics).
- **What was modified / rejected**:
  - **Modified / Fixed**: The prototype service previously accepted incoming reservations unconditionally, creating new reservation records and deducting inventory multiple times for identical orders. I rejected this design and introduced a strict idempotency check:
    1. Added a unique constraint `idx_reservations_order_id` in SQL schema.
    2. Added an idempotent pre-flight check in `reserve_ticket()` that queries existing records and returns the previous active reservation immediately without triggering secondary decrements.
- **Architectural Reflection & Critique**:
  - Blindly executing non-idempotent writes across network boundaries breaks inventory conservation. Implementing idempotency guarantees that transient network retries from the Order service orchestrator never deduct duplicate tickets.

---

### Entry 4: Service Discovery and Team Architecture Contract Alignment
- **Date**: October 7, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "Adapt the inventory service to conform to the shared contract.md and docker-compose.yaml: unify listening ports, environment variables, and Docker health checks."
- **What was accepted**:
  - Reading database and cache connection strings dynamically from `DATABASE_URL` and `REDIS_URL` instead of hardcoding localhost parameters.
  - Adding the standardized `GET /health` and `GET /metrics` endpoints to pass the `x-py-health` probe defined in `docker-compose.yaml`.
- **What was modified / rejected**:
  - **Modified**: The AI originally suggested accepting the concurrency strategy solely via the HTTP request body (`req.strategy`). I modified the router to fall back to the environment variable `INVENTORY_MODE` (`req.strategy or DEFAULT_MODE`).
  - **Why modified**: This decoupling allows team infrastructure automation scripts (`bench_modes.sh`) to toggle between `atomic`, `rowlock`, and `redis` modes during k6 load testing via environment variables without breaking callers.

---

### Entry 5: Automated Financial & Inventory Reconciliation Audit Script
- **Date**: October 7, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "Write an automated reconciliation audit script for Member A to verify that the inventory ledger remains balanced and zero overselling occurs."
- **What was accepted**:
  - The mathematical conservation invariant formula:
    $$\text{total\_capacity} = \text{available\_tickets} + \text{reserved\_tickets} + \text{confirmed\_tickets}$$
    and the strict boundary assertion $\text{available\_tickets} \ge 0$.
- **What was modified / rejected**:
  - **Modified**: The AI initially generated an external standalone Python script requiring host dependencies (`psycopg2`). I modified and encapsulated the verification logic into an executable POSIX shell script (`scripts/reconcile.sh`) that executes a self-contained PL/pgSQL assertion block inside the running Postgres container.
- **Architectural Reflection & Critique**:
  - Relying on local client Python dependencies makes CI/CD verification brittle. Running declarative PL/pgSQL assertions directly against the Postgres container engine ensures zero-dependency execution for GitHub Actions and teammate verification.

---

### Entry 6: Multi-Tenant Database Initialization and Schema Isolation
- **Date**: October 7, 2026
- **Tool**: Google Gemini
- **Prompt / Task**: 
  > "How should the inventory database tables be provisioned in the team repository alongside other microservices?"
- **What was accepted**:
  - The relational schema layout separating aggregate tier balances (`ticket_inventory`) from discrete hold audit trails (`reservations`).
- **What was modified / rejected**:
  - **Rejected**: The AI suggested adding an independent Postgres container instance exclusively for the inventory service inside `docker-compose.yaml`.
  - **Why rejected**: According to the team infrastructure contract maintained by Xiang Wang, all services share a single managed PostgreSQL container with distinct logical databases (`catalog`, `inventory`, `orders`, etc.) to minimize resource overhead during local development. I adapted the migration by appending a database creation directive (`CREATE DATABASE inventory; \connect inventory;`) to the shared `db/init.sql`.
- **Architectural Reflection & Critique**:
  - Service boundary isolation in microservices does not strictly mandate physical database server fragmentation at the local prototype phase; logical database segregation satisfies schema independence while conserving memory on local developer machines.