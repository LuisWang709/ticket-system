# Iteration 1 verification — 2026-10-28

Executed by Codex against the new local checkout based on `a98f7f8`.

## Results

| Check | Observed result |
| --- | --- |
| Standalone Compose configuration | `docker compose -p ticket-b-v1 -f compose.member-b.yaml config -q` passed |
| Build and start | PostgreSQL 16, Redis 7, the existing inventory service, new order service and new payment service all started and became healthy |
| Automated suite | **9 passed, 1 warning in 0.56s** |
| Browser-facing endpoints | Both services returned HTTP 200 for `/health`, `/docs` and `/openapi.json` on ports 18003 and 18004 |
| Existing tracked files | Changed `docker-compose.yaml` (added order, payment, rabbitmq, tests), `stubs/inventory-stub/main.py` (response shape aligned with real inventory), `README.md` and `.gitignore`. Owner (Member D) to be notified in the PR. |

Executed suite:

```bash
docker compose -p ticket-b-v1 -f compose.member-b.yaml --profile test run --build --rm tests
```

The single warning is a dependency deprecation warning about Starlette TestClient's use of an AnyIO BlockingPortal alias. No tests failed.

## What was actually verified

- Real PostgreSQL storage and advisory locks, including four concurrent matching order requests and four concurrent matching payment requests.
- Stable identifiers and results across application recreation using the same database.
- Controlled inventory responses for successful reservation, sold out, timeout and malformed JSON payload structure.
- One actual request through the running order container to A's real inventory service, producing a PENDING order, then a standalone mock payment request.
- Payment does not update or confirm the order in this iteration.

The test runner created and removed a uniquely named test database. The live integration check left one demonstration order and payment in the isolated `ticket-b-v1` databases and reserved one ticket there.

## Not verified / not implemented

- This is not a complete purchase workflow: no payment orchestration, confirmation, compensation, expiry handling, risk assessment, messaging or Outbox.
- A's inventory concurrency, expiration and Redis correctness were not certified by this test suite.
- No load, chaos, broker-failure or end-to-end cancellation tests were run.
- The new tests are run through the standalone Compose configuration; they have not been integrated into the team's existing GitHub CI workflow.
## Manual verification by Dingyao Xue — 2026-11-02

Run on my own machine with the Member B Compose stack.

| Check | Observed result |
| --- | --- |
| Build and start | postgres, redis, inventory, order and payment all reported `Healthy` |
| `POST /orders` (new key) | 200, `status: PENDING`, with an `order_id` and a `reservation_id` from inventory |
| Same key, same body again | Same `order_id` returned |
| Same key, `qty` changed to 2 | 409 |
| `GET /orders/{id}` | `PENDING` |
| `POST /payments` for that order | `SUCCEEDED` |
| `fail_rate` set to 1, new order_id | `FAILED`; the already-paid order still returned `SUCCEEDED` |
| Automated suite, first run | 8 passed, 1 failed: the live test timed out (`httpx.ReadTimeout`) on the payment call; most likely the payment config was still changed from the manual demo |
| Automated suite after resetting payment config to 0/0 | **9 passed, 1 warning** |

The first-run failure shows the gap the next iteration addresses: a slow payment service makes its caller wait until its own timeout.
