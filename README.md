# Ticket Drop Platform

UCD COMP41720 group project (Option A). A concert ticket-drop system built from microservices.

## Quick start

Whole system (one command, as the brief requires):

    docker compose up -d --build

Member B on its own, without the rest of the team's stack:

    docker compose -p ticket-b-v1 -f compose.member-b.yaml up -d --build --wait

The two stacks use different host ports, so they can run side by side.

## Ports

| Service | Internal | Team stack | Member B stack |
| --- | --- | --- | --- |
| catalog | 8000 | 8001 | — |
| inventory | 8000 | 8002 | 18002 |
| order | 8000 | 8003 | 18003 |
| payment | 8000 | 8004 | 18004 |
| gateway (nginx) | 80 | 8080 | — |
| rabbitmq UI | 15672 | 15672 | — |

If the team stack fails to bind a port, something else on the machine already
holds it. Find it with `docker ps` and stop that stack first.

## Useful URLs

| What | Team stack | Member B stack |
| --- | --- | --- |
| order API docs | http://localhost:8003/docs | http://localhost:18003/docs |
| payment API docs | http://localhost:8004/docs | http://localhost:18004/docs |
| inventory API docs | http://localhost:8002/docs | http://localhost:18002/docs |

## Tests

    docker compose --profile test run --build --rm tests

Or against the Member B stack:

    docker compose -p ticket-b-v1 -f compose.member-b.yaml --profile test run --build --rm tests

## Runbook

- **Observe it:** every service exposes `GET /metrics` (Prometheus) and `GET /health`.
- **Break it:** `POST http://localhost:8004/admin/config {"fail_rate": 1, "delay_ms": 5000}`
  makes every new payment slow and failing. Reset with `{"fail_rate": 0, "delay_ms": 0}`.
- **Recover:** orders that timed out stay in `NEW`; retry them with the same
  `Idempotency-Key` and the same body.

## Documentation

| Document | What it is |
| --- | --- |
| `docs/contract.md` | The binding interface contract between the four members |
| `docs/member-b/iteration-1.md` | What Member B's first iteration does and does not do |
| `docs/adr/` | Architecture decision records |

## Team

| Member | Owns |
| --- | --- |
| Tianyue Li | inventory |
| Dingyao Xue | order, payment |
| Hsinyi Lin | risk, notification |
| Xiang Wang | catalog, gateway, compose, observability, CI |
