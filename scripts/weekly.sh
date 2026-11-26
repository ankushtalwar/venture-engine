#!/usr/bin/env bash
# Weekly run: collect from the open sources, analyse, deep-dive the top problems, write the report.
# Schedule with cron, for example every Monday at 06:00:
#   0 6 * * 1  cd /path/to/pain-discovery && scripts/weekly.sh >> data/weekly.log 2>&1
# --batch uses the Message Batches API (half price); each LLM step then waits for its batch (usually < 1 hour).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run paindisc run -s hn -s github -s brave -s greenhouse -s lever -s pricing --budget 25 --batch
uv run paindisc deepdive --top 10 -s hn -s github
uv run paindisc report --top 20
