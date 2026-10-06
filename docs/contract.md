# Team Architecture & Interface Contract

**Version:** 1.0  
**Maintainer:** Xiang Wang  
**Last Updated:** October 5, 2026  

---

## Overview

This specification serves as the binding technical contract for concurrent development across all four team members. Any proposed modifications to interfaces, shared structures, environment variables, or metrics must follow the change protocol:
1. Update this contract document.
2. Notify the entire team via the primary communication channel.
3. Obtain explicit team confirmation and log signatures.
4. Implement code updates only after team consensus.

---

## 1. Codebase & Ownership Matrix

### Directory & File Ownership

| Path / Pattern | Owner | Description |
| :--- | :--- | :--- |
| `services/inventory/` | Tianyue Li | Inventory service: 3 reservation modes, periodic cleanup tasks |
| `scripts/reconcile.sh` | Tianyue Li | Reconciliation script (CI integration managed by Xiang Wang) |
| `services/order/` | Dingyao Xue | Order service, order state machine, Saga orchestrator, Outbox publisher, `resilience.py` |
| `services/payment/` | Dingyao Xue | Mock Payment gateway service |
| `services/risk/` | Hsinyi Lin | AI Risk Assessment service |
| `services/notification/` | Hsinyi Lin | Notification dispatch service |
| `services/catalog/` | Xiang Wang | Event Catalog service |
| `docker-compose.yml`, `gateway/`, `prometheus/`, `grafana/`, `toxiproxy/`, `rabbitmq/`, `loadtest/`, `stubs/`, `.github/`, `scripts/reset.sh`, `scripts/bench_modes.sh`, `scripts/chaos.sh`, `docs/runbook.md`, `docs/contract.md` | Xiang Wang | Infrastructure, API Gateway, Service Mesh/Proxy, Observability, CI/CD, Test Stubs |
| `docs/adr/` | ADR Lead | Architectural Decision Records (One file per ADR) |
| `docs/ai-log/` | All Members | Individual AI interaction logs (`Tianyue_Li.md`, `Dingyao_Xue.md`, `Hsinyi_Lin.md`, `Xiang_Wang.md`), to be consolidated before submission |

### Shared Synchronized Files

To maintain consistency across service boundaries, the following shared files must remain byte-for-byte identical. Synchronized integrity is strictly enforced by CI pipelines.

| Synchronized File Pair | Source of Truth Owner | Synchronizer / Replicator |
| :--- | :--- | :--- |
| `services/inventory/outbox.py` $\leftrightarrow$ `services/order/outbox.py` | Dingyao Xue | Tianyue Li |
| `services/risk/rules.py` $\leftrightarrow$ `services/order/rules.py` | Hsinyi Lin | Dingyao Xue |

---

## 2. Networking, Service Names & Infrastructure Topologies

Inter-service communications must strictly use internal service discovery hostnames formatted as `http://<service_name>:<container_port>`.

### Service Topology Matrix

| Service | Internal Port | Exposed Host Port | Database / Storage |
| :--- | :--- | :--- | :--- |
| `catalog` | 8000 | 8001 | `catalog` (PostgreSQL) |
| `inventory` | 8000 | 8002 | `inventory` (PostgreSQL) + Redis |
| `order` | 8000 | 8003 | `orders` (PostgreSQL) |
| `payment` | 8000 | 8004 | `payments` (PostgreSQL) |
| `risk` | 8000 | 8005 | None (Model artifacts) |
| `notification` | 8000 | 8006 | `notifications` (PostgreSQL) |
| `gateway` (Nginx) | 80 | 8080 | N/A |
| Grafana / Prometheus | 3000 / 9090 | 3000 / 9090 | N/A |
| RabbitMQ Management / Metrics | 15672 / 15692 | 15672 / 15692 | N/A |
| Toxiproxy | 8474 | 8474 | N/A |
| Redis | 6379 | Internal Only | N/A |

### Proxy Proxy Forwarding (Toxiproxy)

- `toxiproxy:9001` $\rightarrow$ `payment:8000`
- `toxiproxy:9002` $\rightarrow$ `risk:8000`

---

## 3. Environment Variables Configuration

| Service | Variable Name | Default Value / Description |
| :--- | :--- | :--- |
| All DB-enabled services | `DATABASE_URL` | `postgresql://postgres:postgres@postgres:5432/<db_name>` |
| `inventory`, `order`, `notification` | `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` |
| `inventory` | `HOLD_SECONDS` | Reservation hold duration in seconds (Demo: `30`, Production: `600`) |
| `inventory` | `INVENTORY_MODE` | Inventory deduction strategy (`atomic` \| `rowlock` \| `redis`, Default: `atomic`) |
| `inventory` | `REDIS_URL` | `redis://redis:6379/0` |
| `order` | `INVENTORY_URL` | Default: `http://inventory:8000` |
| `order` | `PAYMENT_URL` | Default: `http://toxiproxy:9001` |
| `order` | `RISK_URL` | Default: `http://toxiproxy:9002` |
| `payment` | `DELAY_MS`, `FAIL_RATE` | Initial latency and failure rates; dynamic updates via `POST /admin/config` |
| `risk` | `MODEL_VERSION` | Default: `v1-logreg` |
| `risk` | `TOKEN_BUDGET_PER_MIN` | Rate limit budget (Default: `60000`) |
| `risk` | `DELAY_MS` | Simulated AI inference delay (Default: `0`) |

---

## 4. HTTP API Specifications

All services must expose operational endpoints:
- `GET /health` - Health check status
- `GET /metrics` - Prometheus metrics payload

---

### 4.1 Catalog Service (`catalog`)

#### `GET /events`
* **Description:** Retrieve a list of all active events.
* **Response `200 OK`:** Array of event objects.

#### `GET /events/{id}`
* **Description:** Retrieve details for a specific event including ticket tiers.
* **Response `200 OK`:** Event details with tier list.
* **Response `404 Not Found`:** Event ID does not exist.

---

### 4.2 Order Service (`order`)

#### `POST /orders`
* **Description:** Create a new order (requires idempotency protection).
* **Headers:**
  - `Idempotency-Key` *(required)*: Unique UUID string.
* **Request Body:**
```json
{
  "user_id": "string",
  "tier_id": "string",
  "qty": 1,
  "amount_cents": 10000,
  "clicks_last_1s": 2,
  "requests_last_10s": 5,
  "account_age_days": 120,
  "ms_page_to_click": 1500,
  "accounts_on_device": 1
}