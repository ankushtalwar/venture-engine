#!/usr/bin/env bash
# Local development: API with auto-reload on 127.0.0.1:8101, Vite dev server on 5173 (proxies /api to the API).
#   scripts/dev.sh            # serve data/paindisc.sqlite (or DATABASE_URL if set)
#   scripts/dev.sh --demo     # rebuild a demo SQLite (default data/demo.sqlite) from the synthetic fixtures, serve it
# Env: DEV_API_PORT (8101), DEV_UI_PORT (5173), DEV_DB (SQLite file to serve / seed with --demo).
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

api_port=${DEV_API_PORT:-8101}
ui_port=${DEV_UI_PORT:-5173}
db=${DEV_DB:-}

if [ "${1:-}" = "--demo" ]; then
  db=${db:-data/demo.sqlite}
  echo "dev: seeding $db from synthetic fixtures (mock LLM)"
  LLM_MOCK=true uv run paindisc demo --db "$db"
fi

if [ ! -d frontend/node_modules ]; then
  echo "dev: installing frontend dependencies"
  npm --prefix frontend ci --no-audit --no-fund
fi

api_args=(serve --host 127.0.0.1 --port "$api_port" --reload)
if [ -n "$db" ]; then api_args+=(--db "$db"); fi

# Each server runs in its own process group so one signal stops it with all its children (uv -> uvicorn
# reloader -> worker, npm -> vite), whether the script gets Ctrl-C or a plain kill. stdin is /dev/null so a
# background process group never stops on a terminal read.
set -m
uv run paindisc "${api_args[@]}" </dev/null &
api_pid=$!
VITE_API_TARGET="http://127.0.0.1:$api_port" npm --prefix frontend run dev -- --port "$ui_port" </dev/null &
ui_pid=$!
set +m

stopping=0
# shellcheck disable=SC2317  # reached via the EXIT trap
cleanup() {
  [ "$stopping" = 1 ] && return
  stopping=1
  kill -TERM -- "-$api_pid" "-$ui_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "dev: API http://127.0.0.1:$api_port/api/health  UI http://127.0.0.1:$ui_port  (Ctrl-C stops both)"
# Exit (and stop the other one) as soon as either server dies. Polling keeps this working on macOS bash 3.2.
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$ui_pid" 2>/dev/null; do sleep 1; done
echo "dev: a server exited; stopping" >&2
exit 1
