#!/usr/bin/env bash
# Weekly run: collect -> analyse -> deep-dive the top problems -> trends -> report -> deliver.
#
# Runs in the compose `scheduler` container (supercronic, WEEKLY_CRON), or from a host crontab, e.g. Monday 06:00:
#   0 6 * * 1  cd /path/to/venture-engine && scripts/weekly.sh >> data/weekly.log 2>&1
#
# Knobs (environment, all optional):
#   WEEKLY_SOURCES          collectors for the main run      (default: "hn github brave greenhouse lever pricing")
#   WEEKLY_BUDGET           queries per query-driven source  (default: 25)
#   WEEKLY_BATCH            true = Message Batches API: half price, each LLM step waits for its batch (default: true)
#   WEEKLY_DEEPDIVE_TOP     clusters to deep-dive            (default: 10)
#   WEEKLY_DEEPDIVE_SOURCES collectors for the deep dive     (default: "hn github")
#   WEEKLY_AGENTIC          true = tool-using Claude deep dive that logs every query (default: false)
#   WEEKLY_REPORT_TOP       cards in the report              (default: 20)
#   WEEKLY_DELIVER_TOP      cards in the Slack digest        (default: 10)
#
# `run` and `report` must succeed. `deepdive`, `trends` and `deliver` are soft: a failure is logged, the
# remaining steps still run (so a report is still written), and the script exits 1 at the end.
# Delivery is a no-op with a message unless SLACK_WEBHOOK_URL is set AND the registry's `slack` entry is allowed.
set -euo pipefail
cd "$(dirname "$0")/.."

log() { printf '[%s] weekly: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
truthy() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac; }

# Inside the image `paindisc` is on PATH; on a dev machine fall back to uv.
if command -v paindisc >/dev/null 2>&1; then
  pd() { paindisc "$@"; }
else
  pd() { uv run --quiet paindisc "$@"; }
fi

failed=""
soft() {  # soft <step> <args...>: run a step, remember a failure instead of aborting
  local step=$1; shift
  log "$step"
  if ! pd "$@"; then
    log "$step FAILED (continuing)"
    failed="$failed $step"
  fi
}

run_sources=${WEEKLY_SOURCES:-hn github brave greenhouse lever pricing}
dive_sources=${WEEKLY_DEEPDIVE_SOURCES:-hn github}
run_args=(--budget "${WEEKLY_BUDGET:-25}")
for s in $run_sources; do run_args+=(-s "$s"); done
dive_args=(--top "${WEEKLY_DEEPDIVE_TOP:-10}")
for s in $dive_sources; do dive_args+=(-s "$s"); done
batch=off; if truthy "${WEEKLY_BATCH:-true}"; then run_args+=(--batch); batch=on; fi
mode=classic; if truthy "${WEEKLY_AGENTIC:-false}"; then dive_args+=(--agentic); mode=agentic; fi

started=$(date +%s)
log "start (sources: $run_sources; batch: $batch; deep dive: $mode on $dive_sources)"

log "run"
pd run "${run_args[@]}"

soft deepdive deepdive "${dive_args[@]}"
soft trends trends

log "report"
pd report --top "${WEEKLY_REPORT_TOP:-20}"

soft deliver deliver --top "${WEEKLY_DELIVER_TOP:-10}"

if [ -n "$failed" ]; then
  log "finished with failures:$failed ($(( $(date +%s) - started ))s)"
  exit 1
fi
log "done in $(( $(date +%s) - started ))s"
