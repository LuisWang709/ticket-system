#!/usr/bin/env bash
# Checks every service answers according to docs/contract.md. Run after `docker compose up`.
set -uo pipefail
FAIL=0
expect() { # name expected_status curl-args...
  local name=$1 want=$2; shift 2
  local got; got=$(curl -s -o /dev/null -w '%{http_code}' "$@")
  if [ "$got" = "$want" ]; then echo "PASS  $name"; else echo "FAIL  $name: got $got want $want"; FAIL=1; fi
}
expect "catalog health"    200 localhost:8001/health
expect "catalog metrics"   200 localhost:8001/metrics
expect "catalog events"    200 localhost:8001/events
expect "catalog event 1"   200 localhost:8001/events/1
expect "catalog event 999" 404 localhost:8001/events/999
expect "gateway health"    200 localhost:8080/health
expect "gateway events"    200 localhost:8080/api/events
[ $FAIL -eq 0 ] && echo "SMOKE OK" || { echo "SMOKE FAILED"; exit 1; }