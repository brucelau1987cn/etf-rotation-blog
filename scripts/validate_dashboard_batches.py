#!/usr/bin/env python3
"""Validate cross-file dashboard batch consistency before static publication."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"


@dataclass
class CheckResult:
    status: str
    errors: list[str]
    warnings: list[str]
    batches: dict[str, Any]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing file: {display_path(path)}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {display_path(path)}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"root must be object: {display_path(path)}")
    return payload


def date_prefix(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    raw = value[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def require_date(errors: list[str], label: str, value: Any) -> str | None:
    parsed = date_prefix(value)
    if parsed is None:
        errors.append(f"{label} missing or invalid: {value!r}")
    return parsed


def validate(data_dir: Path = DATA) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    batches: dict[str, Any] = {}
    try:
        garden = load_json(data_dir / "garden-recommendations.json")
        a_pool = load_json(data_dir / "etf-garden-pool.json")
        a_mid = load_json(data_dir / "a-share-mid-macro.json")
        shadow = load_json(data_dir / "model-lab/a-share-shadow.json")
        us = load_json(data_dir / "us-etf-garden.json")
        us_pool = load_json(data_dir / "us-etf-pool.json")
        us_macro = load_json(data_dir / "us-macro-dashboard.json")
    except ValueError as exc:
        return CheckResult("error", [str(exc)], warnings, batches)

    a_date = require_date(errors, "A recommendations date", garden.get("date"))
    a_applies = require_date(errors, "A recommendations applies_to", garden.get("applies_to"))
    a_level = require_date(errors, "A recommendation level_data_as_of", garden.get("level_data_as_of"))
    pool_eval = require_date(errors, "A pool evaluation_date", a_pool.get("evaluation_date"))
    pool_latest = require_date(errors, "A pool latest_trade_date", a_pool.get("latest_trade_date"))
    mid_generated = require_date(errors, "A mid-macro generated_at", a_mid.get("generated_at"))
    shadow_latest = require_date(errors, "A shadow latest_trade_date", shadow.get("latest_trade_date"))
    action_dates = sorted({
        parsed
        for section in ("plant", "harvest", "watch")
        for item in garden.get(section, []) if isinstance(item, dict)
        if (parsed := date_prefix(item.get("price_date")))
    })
    a_expected = [x for x in (a_date, a_applies, a_level, pool_eval, pool_latest, mid_generated, shadow_latest) if x]
    if len(set(a_expected)) > 1:
        errors.append(
            "A-share batch mismatch: "
            f"recommendations={a_date}, applies_to={a_applies}, levels={a_level}, "
            f"pool_evaluation={pool_eval}, pool_latest={pool_latest}, mid_macro={mid_generated}, shadow={shadow_latest}"
        )
    if action_dates and (len(action_dates) != 1 or action_dates[0] != a_date):
        errors.append(f"A action price dates mismatch: recommendation={a_date}, actions={action_dates}")
    stage = str(garden.get("stage") or "")
    if stage.startswith("22:00") and a_date != pool_latest:
        errors.append(f"A 22:00 final stage requires final pool date {a_date}, got {pool_latest}")

    us_date = require_date(errors, "US garden date", us.get("date"))
    us_model = require_date(errors, "US pool model_date", us_pool.get("model_date"))
    us_quote = require_date(errors, "US pool quote_trade_date", us_pool.get("quote_trade_date"))
    macro_generated = require_date(errors, "US macro generated_at", us_macro.get("generated_at"))
    market_dates = sorted({
        parsed for item in (us_macro.get("market") or {}).values() if isinstance(item, dict)
        if (parsed := date_prefix(item.get("date")))
    })
    macro_primary = max(market_dates) if market_dates else None
    if macro_primary is None:
        errors.append("US macro market dates are missing")
    us_expected = [x for x in (us_date, us_model, us_quote, macro_generated, macro_primary) if x]
    if len(set(us_expected)) > 1:
        errors.append(
            "US batch mismatch: "
            f"garden={us_date}, model={us_model}, quote={us_quote}, "
            f"macro_generated={macro_generated}, macro_primary={macro_primary}"
        )
    if us.get("session_state") != us_pool.get("session_state"):
        errors.append(
            f"US session_state mismatch: garden={us.get('session_state')!r}, pool={us_pool.get('session_state')!r}"
        )

    batches["a_share"] = {
        "date": a_date,
        "stage": garden.get("stage"),
        "action_dates": action_dates,
        "pool_latest": pool_latest,
        "mid_macro_date": mid_generated,
        "shadow_date": shadow_latest,
    }
    batches["us"] = {
        "date": us_date,
        "stage": us.get("stage"),
        "session_state": us.get("session_state"),
        "pool_date": us_quote,
        "macro_primary_date": macro_primary,
    }
    return CheckResult("ok" if not errors else "error", errors, warnings, batches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    result = validate(args.data_dir)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
