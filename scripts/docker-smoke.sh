#!/usr/bin/env bash
# Docker smoke test: build the image, start db + migrate + api with LLM_MOCK=true, seed the synthetic demo into
# Postgres, check the API, the UI and the scheduler, then tear everything down (containers AND volumes).
#   scripts/docker-smoke.sh
# Env: SMOKE_PROJECT (compose project, default paindisc-devops), SMOKE_API_PORT (8000), SMOKE_DB_PORT (55432),
#      SMOKE_IMAGE (paindisc:smoke), SMOKE_SKIP_BUILD=1 to test an image you already built,
#      SMOKE_E2E=1 to also run the Playwright `docker` project against the stack (needs node + uv),
#      SMOKE_KEEP=1 to leave the stack running for a look (tear down later with: docker compose -p <project> down -v).
# Never reads the real .env: the containers get a throwaway env file with LLM_MOCK=true only.
set -euo pipefail
cd "$(dirname "$0")/.."

project=${SMOKE_PROJECT:-paindisc-devops}
if [ "$project" = "paindisc" ]; then
  echo "smoke: refusing project 'paindisc': that's the real stack, and the smoke test deletes its volumes" >&2
  exit 2
fi
export PAINDISC_PORT=${SMOKE_API_PORT:-8000}
export POSTGRES_PORT=${SMOKE_DB_PORT:-55432}
export PAINDISC_IMAGE=${SMOKE_IMAGE:-paindisc:smoke}
export LLM_MOCK=true

log() { printf '[smoke] %s\n' "$*"; }
fail() { printf '[smoke] FAIL: %s\n' "$*" >&2; exit 1; }

docker info >/dev/null 2>&1 || fail "the Docker daemon isn't reachable"
command -v curl >/dev/null || fail "curl is required"
command -v python3 >/dev/null || fail "python3 is required (JSON checks)"

# One run per project. A second run on the same project would share its containers and, on exit, `down -v`
# the first run's stack mid-test. The lock is a directory holding the owner's pid (stale locks are taken over).
lock="${TMPDIR:-/tmp}/paindisc-smoke-$project.lock"
if ! mkdir "$lock" 2>/dev/null; then
  other=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -z "$other" ]; then sleep 1; other=$(cat "$lock/pid" 2>/dev/null || true); fi  # owner may be mid-write
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    echo "[smoke] another smoke run (pid $other) is using project '$project'; wait for it, or set SMOKE_PROJECT and the SMOKE_*_PORT vars" >&2
    exit 3
  fi
  rm -rf "$lock"
  mkdir "$lock" 2>/dev/null || { echo "[smoke] could not take the lock $lock" >&2; exit 3; }
fi
echo "$$" > "$lock/pid"
if [ -n "$(docker compose -p "$project" ps -a -q 2>/dev/null)" ]; then
  rm -rf "$lock"
  echo "[smoke] project '$project' already has containers (a kept smoke stack?). Remove them first: docker compose -p $project down -v" >&2
  exit 3
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/paindisc-smoke.XXXXXX")
printf 'LLM_MOCK=true\n' > "$tmp/smoke.env"
export PAINDISC_ENV_FILE="$tmp/smoke.env"

compose() { docker compose -p "$project" --env-file "$tmp/smoke.env" "$@"; }

# Installed only now: from here on this run owns the project's stack, so tearing it down is ours to do.
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    log "logs (last 60 lines per service):"
    compose logs --no-color --tail 60 db migrate api scheduler 2>/dev/null || true
  fi
  if [ "${SMOKE_KEEP:-}" = "1" ] && [ "$status" -eq 0 ]; then
    log "kept running: http://127.0.0.1:$PAINDISC_PORT  (docker compose -p $project down -v to remove)"
  else
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp" "$lock"
  exit "$status"
}
trap cleanup EXIT

if [ "${SMOKE_SKIP_BUILD:-}" = "1" ]; then
  docker image inspect "$PAINDISC_IMAGE" >/dev/null 2>&1 || fail "SMOKE_SKIP_BUILD=1 but $PAINDISC_IMAGE doesn't exist"
  log "using existing $PAINDISC_IMAGE"
else
  log "build $PAINDISC_IMAGE"
  compose build api
fi

log "up: db, migrate, api"
compose up -d --wait --wait-timeout 180 api

db_url="postgresql://paindisc:paindisc@db:5432/paindisc"
log "seed the synthetic demo into Postgres"
compose run --rm --no-deps -T api paindisc demo --database-url "$db_url"

base="http://127.0.0.1:$PAINDISC_PORT"

log "GET /api/health"
curl -fsS "$base/api/health" -o "$tmp/health.json" || fail "/api/health"
python3 - "$tmp/health.json" <<'PY' || fail "unexpected /api/health"
import json, sys
h = json.load(open(sys.argv[1]))
print("  ", h)
assert h.get("status") == "ok", h
assert h.get("store") == "postgres", h
assert h.get("llm_mock") is True, h
PY

log "GET /api/clusters"
curl -fsS "$base/api/clusters" -o "$tmp/clusters.json" || fail "/api/clusters"
python3 - "$tmp/clusters.json" <<'PY' || fail "unexpected /api/clusters"
import json, sys
c = json.load(open(sys.argv[1]))
items = c.get("items") or []
print(f"   total={c.get('total')} first={items[0]['title'] if items else None!r}")
assert c.get("total", 0) > 0 and items, "demo seeded no clusters"
assert {"id", "score", "confidence"} <= set(items[0]), items[0]
PY

log "GET / (review UI), a built asset, and the SPA fallback"
curl -fsS "$base/" -o "$tmp/index.html" || fail "/ did not serve the UI"
grep -qi "<html" "$tmp/index.html" || fail "/ is not HTML"
asset=$(grep -oE '/assets/[^"]+\.js' "$tmp/index.html" | head -n 1 || true)
[ -n "$asset" ] || fail "index.html references no built /assets/*.js (is frontend/dist a real build?)"
curl -fsS "$base$asset" -o /dev/null || fail "built asset $asset not served"
curl -fsS "$base/clusters/does-not-exist" -o "$tmp/deep.html" || fail "SPA fallback failed"
cmp -s "$tmp/index.html" "$tmp/deep.html" || fail "deep link did not serve index.html"

log "port is bound to 127.0.0.1 only"
compose port api 8000 | grep -q "^127.0.0.1:" || fail "api port is not bound to 127.0.0.1: $(compose port api 8000)"

log "trends + deliver (Slack unset: must skip, exit 0)"
compose run --rm --no-deps -T api paindisc trends
out=$(compose run --rm --no-deps -T api paindisc deliver)
echo "   $out"
case "$out" in *"skipped: SLACK_WEBHOOK_URL is not set"*) ;; *) fail "deliver did not skip" ;; esac

if [ "${SMOKE_E2E:-}" = "1" ]; then
  # Playwright's `docker` project against this stack; it reseeds Postgres through the published port.
  log "e2e: Playwright docker project against $base"
  [ -d e2e/node_modules ] || npm --prefix e2e ci --no-audit --no-fund
  E2E_DOCKER=1 E2E_EXTERNAL_URL="$base" E2E_SKIP_BUILD=1 \
    E2E_DATABASE_URL="postgresql://paindisc:paindisc@127.0.0.1:$POSTGRES_PORT/paindisc" \
    npm --prefix e2e run test:docker || fail "e2e against the compose stack"
fi

log "scheduler starts and accepts its schedule"
compose up -d scheduler
sleep 3
[ "$(docker inspect -f '{{.State.Running}}' "$(compose ps -q scheduler)")" = "true" ] || fail "scheduler is not running"
compose logs --no-color scheduler | grep -q "scheduler: weekly.sh on" || fail "scheduler did not start"

log "OK"
