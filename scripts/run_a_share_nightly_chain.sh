#!/usr/bin/env bash
# Run A-share nightly pre-stages only on exchange-open days.
set -euo pipefail
cd /root/projects/etf-rotation-blog
# The same exchange calendar used by the publishing gate is authoritative.
# Unavailable calendar fails closed before any cache or formal-pool writes.
if python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
from scripts.check_a_share_cron_gate import is_trading_day
now = datetime.now(ZoneInfo('Asia/Shanghai'))
opened, source = is_trading_day(now.date().isoformat())
if opened is False:
    print(f'{{"status":"idempotent","reason":"exchange calendar is closed","date":"{now.date()}","source":"{source}"}}')
    raise SystemExit(0)
if opened is None:
    print(f'STAGING BLOCKER: exchange calendar unavailable for {now.date()} ({source})')
    raise SystemExit(2)
raise SystemExit(10)
PY
then
    exit 0
else
    status=$?
    if [ "$status" -ne 10 ]; then exit "$status"; fi
fi
# Keep the scheduler-facing timeout above the runner's total budget.
exec timeout 4500s python3 scripts/run_a_share_nightly_stage.py --stage precheck-cache --timeout 3900
