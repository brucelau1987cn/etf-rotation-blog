"""Tests for the nightly chain calendar gate and its bounded second attempt.

The chain must never lose a whole night to a transient calendar outage
(2026-10-08), while still failing closed when the calendar genuinely cannot be
resolved. A definite "closed" verdict is final and must not be retried.
"""
from __future__ import annotations

import contextlib
import io
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_nightly_chain_calendar as chain_gate  # noqa: E402

PROCEED = chain_gate.PROCEED
NOW = datetime(2026, 10, 8, 21, 20)


def _run_main(monkeypatch, lookup, attempts=2):
    """Drive main() with a scripted calendar lookup and capture its output."""
    monkeypatch.setattr(sys, "argv", ["prog", "--attempts", str(attempts), "--retry-delay", "0"])
    # Patch the module-level lookup main() resolves, not evaluate() itself.
    monkeypatch.setattr(chain_gate, "is_trading_day", lookup)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = chain_gate.main()
    return code, buffer.getvalue()


def test_open_day_proceeds():
    code, receipt = chain_gate.evaluate(NOW, lookup=lambda day: (True, "d1_exchange_calendar"))
    assert code == PROCEED
    assert receipt["status"] == "open"
    assert receipt["date"] == "2026-10-08"


def test_closed_day_is_idempotent_skip():
    code, receipt = chain_gate.evaluate(NOW, lookup=lambda day: (False, "d1_exchange_calendar"))
    assert code == 0
    assert receipt["status"] == "idempotent"
    assert receipt["reason"] == "exchange calendar is closed"


def test_unavailable_calendar_fails_closed():
    code, receipt = chain_gate.evaluate(
        NOW, lookup=lambda day: (None, "unavailable(cf_baostock: no verdict)")
    )
    assert code == 2
    assert receipt["status"] == "blocked"
    assert "unavailable" in receipt["reason"]
    # The receipt keeps the source detail so a recurrence is diagnosable.
    assert "cf_baostock" in receipt["source"]


def test_second_attempt_recovers_a_transient_outage(monkeypatch):
    """One transient blip must not cost the period's output."""
    verdicts = [(None, "unavailable(cf_baostock: no verdict)"), (True, "cf_baostock")]
    calls = []

    def lookup(day):
        calls.append(day)
        return verdicts[min(len(calls) - 1, len(verdicts) - 1)]

    code, output = _run_main(monkeypatch, lookup)
    assert code == PROCEED
    assert len(calls) == 2, "the second attempt must run"
    assert "recovered_after_attempt" in output


def test_closed_verdict_is_never_retried(monkeypatch):
    calls = []

    def lookup(day):
        calls.append(day)
        return (False, "d1_exchange_calendar")

    code, output = _run_main(monkeypatch, lookup, attempts=3)
    assert code == 0
    assert len(calls) == 1, "a closed day must skip immediately, not retry"
    assert "idempotent" in output


def test_all_attempts_failing_emits_staging_blocker(monkeypatch):
    def lookup(day):
        return (None, "unavailable(cf_baostock: no verdict)")

    code, output = _run_main(monkeypatch, lookup)
    assert code == 2
    assert "STAGING BLOCKER" in output
