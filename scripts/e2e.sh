#!/usr/bin/env bash
# End-to-end browser tests: build the review UI, then run the Playwright suite in e2e/ (owned by integration-tester).
# The Playwright config starts and seeds its own API (see e2e/README.md); this script only prepares and runs it.
#   scripts/e2e.sh                 # all e2e tests
#   scripts/e2e.sh -- --headed     # extra args after -- go to Playwright
# Env: E2E_INSTALL_BROWSERS=true installs Chromium (+ OS deps on Linux) first; CI sets it.
set -euo pipefail
cd "$(dirname "$0")/.."

export LLM_MOCK=true   # e2e never calls a real model

if [ ! -d frontend ] || [ ! -d e2e ]; then
  echo "e2e: frontend/ and e2e/ must both exist" >&2
  exit 1
fi

if [ ! -d frontend/node_modules ]; then npm --prefix frontend ci --no-audit --no-fund; fi
echo "e2e: building frontend"
npm --prefix frontend run build

if [ ! -d e2e/node_modules ]; then npm --prefix e2e ci --no-audit --no-fund; fi
case "$(printf '%s' "${E2E_INSTALL_BROWSERS:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes)
    if [ "$(uname -s)" = "Linux" ]; then
      (cd e2e && npx playwright install --with-deps chromium)
    else
      (cd e2e && npx playwright install chromium)
    fi
    ;;
esac

if [ "${1:-}" = "--" ]; then shift; fi
echo "e2e: running Playwright"
npm --prefix e2e test -- "$@"
