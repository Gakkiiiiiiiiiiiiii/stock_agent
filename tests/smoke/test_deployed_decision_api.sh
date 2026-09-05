#!/usr/bin/env sh
set -eu
: "${BASE_URL:=http://127.0.0.1:18000}"
curl -fsS "$BASE_URL/health/live" >/dev/null
curl -fsS "$BASE_URL/health/ready" >/dev/null
curl -fsS "$BASE_URL/health/formal-decision-ready" >/tmp/formal-readiness.json
test -s /tmp/formal-readiness.json
payload='{"task_type":"daily-market-decision","objective":"deterministic smoke","subjects":["CN.A.600519"],"context":{"skill":"daily-market-decision"},"as_of":"2027-01-01T00:00:00Z","portfolio_id":"smoke","idempotency_key":"smoke-key"}'
decision=$(curl -fsS -X POST "$BASE_URL/api/v2/decisions" -H 'content-type: application/json' -d "$payload")
echo "$decision" | grep -q '"status":"HOLD"'
decision_id=$(echo "$decision" | sed -n 's/.*"decision_id":"\([^"]*\)".*/\1/p')
curl -fsS "$BASE_URL/api/v2/decisions/$decision_id/snapshot" >/tmp/decision-snapshot.json
curl -fsS -X POST "$BASE_URL/api/v2/decisions/$decision_id/replay" -H 'content-type: application/json' -d '{"mode":"EXACT_REPLAY"}' >/tmp/decision-replay.json
grep -q '"status"' /tmp/decision-replay.json
