#!/usr/bin/env python3
"""Calendar gate for the A-share nightly chain, with a bounded second attempt.

The chain must not lose a whole night to a transient calendar outage. The gate
itself retries each source and the fallback chain is multi-source; this adds a
delayed second attempt at the chain level, still comfortably inside the stage
window (21:20 -> 22:00 content), so one transient failure does not cost the
period's output.

Exit codes (contract consumed by run_a_share_nightly_chain.sh):
  0  = exchange closed            -> idempotent skip
  2  = calendar unavailable       -> STAGING BLOCKER
  10 = proceed with the chain

Only an ``unavailable`` verdict is retried. A definite ``closed`` verdict is
final and returns immediately: retrying it would delay the skip for no reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_a_share_cron_gate import is_trading_day  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")
PROCEED = 10


def evaluate(now: datetime, lookup=is_trading_day) -> tuple[int, dict[str, object]]:
    """Return the chain exit code plus the receipt for this attempt."""
    opened, source = lookup(now.date().isoformat())
    if opened is False:
        return 0, {
            "status": "idempotent",
            "reason": "exchange calendar is closed",
            "date": str(now.date()),
            "source": source,
        }
    if opened is None:
        return 2, {
            "status": "blocked",
            "reason": f"exchange calendar unavailable for {now.date()}",
            "date": str(now.date()),
            "source": source,
        }
    return PROCEED, {"status": "open", "date": str(now.date()), "source": source}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=2, help="calendar attempts before failing closed")
    parser.add_argument("--retry-delay", type=float, default=120.0, help="seconds between attempts")
    args = parser.parse_args()

    attempts = max(1, args.attempts)
    last: tuple[int, dict[str, object]] = (2, {})
    for attempt in range(attempts):
        now = datetime.now(CN)
        # Resolve the lookup at call time so tests can substitute the calendar.
        code, receipt = evaluate(now, lookup=is_trading_day)
        if code != 2:
            if attempt and receipt.get("status") == "open":
                receipt["recovered_after_attempt"] = attempt + 1
            print(json.dumps(receipt, ensure_ascii=False))
            return code
        last = (code, receipt)
        if attempt < attempts - 1:
            time.sleep(max(0.0, args.retry_delay))

    _, receipt = last
    print(f"STAGING BLOCKER: {receipt.get('reason', 'exchange calendar unavailable')} ({receipt.get('source', 'unknown')})")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
