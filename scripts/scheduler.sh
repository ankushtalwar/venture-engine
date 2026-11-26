#!/usr/bin/env bash
# Entry point of the compose `scheduler` container: runs scripts/weekly.sh on WEEKLY_CRON with supercronic.
#   WEEKLY_CRON  five-field cron expression (default "0 6 * * 1" = Monday 06:00)
#   WEEKLY_TZ    IANA time zone the schedule is read in (default UTC), e.g. Asia/Kolkata
# supercronic logs every run to stdout (`docker compose logs scheduler`) and never starts a run while the
# previous one is still going.
set -euo pipefail
cd "$(dirname "$0")/.."

schedule=${WEEKLY_CRON:-0 6 * * 1}
tz=${WEEKLY_TZ:-UTC}
crontab=$(mktemp "${TMPDIR:-/tmp}/paindisc-crontab.XXXXXX")

{
  printf 'CRON_TZ=%s\n' "$tz"
  printf '%s %s/scripts/weekly.sh\n' "$schedule" "$PWD"
} > "$crontab"

if ! supercronic -test "$crontab" >/dev/null 2>&1; then
  echo "scheduler: invalid WEEKLY_CRON='$schedule' or WEEKLY_TZ='$tz'" >&2
  supercronic -test "$crontab" >&2 || true
  exit 2
fi

echo "scheduler: weekly.sh on '$schedule' ($tz)"
exec supercronic -passthrough-logs "$crontab"
