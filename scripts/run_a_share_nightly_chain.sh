#!/usr/bin/env bash
# Run A-share nightly pre-stages only on exchange-open days.
set -euo pipefail
cd /root/projects/etf-rotation-blog
# The same exchange calendar used by the publishing gate is authoritative.
# Unavailable calendar fails closed before any cache or formal-pool writes.
# The gate loads the CF BaoStock credentials, retries each source, and allows a
# bounded second attempt, so one transient blip no longer costs the whole
# night's output (2026-10-08). A definite "closed" verdict is never retried.
set +e
python3 scripts/check_nightly_chain_calendar.py --attempts 2 --retry-delay 120
status=$?
set -e
if [ "$status" -ne 10 ]; then exit "$status"; fi
# Keep the scheduler-facing timeout above the runner's total budget.
exec timeout 4500s python3 scripts/run_a_share_nightly_stage.py --stage precheck-cache --timeout 3900
