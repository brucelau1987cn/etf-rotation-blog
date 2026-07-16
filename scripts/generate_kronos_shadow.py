#!/usr/bin/env python3
"""Generate a deterministic Kronos ETF forecast sidecar.

The artifact is display-and-audit only. It never imports or mutates production
recommendations, scores, weights, actions, positions, or execution rules.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import random
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DB = ROOT / "data/local/etf-compass.db"
DEFAULT_OUT = ROOT / "public/data/model-lab/a-share-kronos-shadow.json"
DEFAULT_HISTORY = ROOT / "data/local/model-lab/a-share-kronos-shadow-history.jsonl"
DEFAULT_RUNTIME = Path("/root/.cache/etf-kronos")

MODEL_ID = "NeoQuasar/Kronos-mini"
MODEL_REVISION = "f4e68697d9d5aed55cef5c96aabc3376bcad9f81"
TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-2k"
TOKENIZER_REVISION = "26966d0035065a0cae0ebad7af8ece35bc1fb51c"
KRONOS_CODE_REVISION = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
SEED = 20260716
HORIZON = 5
LOOKBACK = 256
MINIMUM_HISTORY = 96
EXPECTED_SYMBOLS = 89


def finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rotation_universe() -> list[dict[str, str]]:
    from scripts.generate_garden_pool import GARDEN_POOL

    return [
        {"symbol": str(item["code"]), "name": str(item["name"])}
        for item in GARDEN_POOL
        if item.get("asset_layer", "rotation") == "rotation"
    ]


def load_frames(
    db_path: Path,
    universe: list[dict[str, str]],
    lookback: int = LOOKBACK,
    minimum_history: int = MINIMUM_HISTORY,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    symbols = [item["symbol"] for item in universe]
    names = {item["symbol"]: item["name"] for item in universe}
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"""SELECT symbol,trade_date,open,high,low,close FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol,trade_date
                ORDER BY CASE source WHEN 'iwencai' THEN 1 WHEN 'stock-api' THEN 2 WHEN 'tencent' THEN 3 ELSE 9 END,
                         fetched_at DESC
              ) rn
              FROM daily_bars
              WHERE market IN ('A','XSHG','XSHE') AND adjustment='qfq' AND is_final=1
                AND symbol IN ({placeholders})
            ) WHERE rn=1 ORDER BY symbol,trade_date""",
            symbols,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["symbol"])].append(dict(row))
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        data = grouped.get(symbol, [])
        if len(data) < minimum_history:
            continue
        frame = pd.DataFrame(data).tail(lookback).copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        frame = frame.set_index("trade_date").sort_index()
        for column in ("open", "high", "low", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[["open", "high", "low", "close"]].isna().any().any():
            continue
        values = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            continue
        frames[symbol] = frame
    return frames, names


def next_cn_sessions(after: date, count: int = HORIZON) -> list[pd.Timestamp]:
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("baostock is required for the CN exchange calendar") from exc
    end = after + timedelta(days=max(20, count * 5))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock calendar login failed: {login.error_msg}")
        try:
            result = bs.query_trade_dates(
                start_date=(after + timedelta(days=1)).isoformat(),
                end_date=end.isoformat(),
            )
            sessions: list[pd.Timestamp] = []
            while result.error_code == "0" and result.next():
                row = result.get_row_data()
                if len(row) > 1 and row[1] == "1":
                    sessions.append(pd.Timestamp(row[0]))
        finally:
            bs.logout()
    if len(sessions) < count:
        raise RuntimeError(f"exchange calendar returned {len(sessions)}/{count} future sessions")
    return sessions[:count]


def input_fingerprint(
    frames: dict[str, pd.DataFrame], future_sessions: list[pd.Timestamp], parameters: dict[str, Any]
) -> str:
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode())
    for symbol in sorted(frames):
        digest.update(symbol.encode())
        frame = frames[symbol]
        digest.update(frame.index.strftime("%Y-%m-%d").str.cat(sep=",").encode())
        digest.update(np.asarray(frame[["open", "high", "low", "close"]], dtype="<f8").tobytes())
    digest.update(",".join(item.date().isoformat() for item in future_sessions).encode())
    return digest.hexdigest()


def validate_raw_prediction(prediction: pd.DataFrame, horizon: int = HORIZON) -> tuple[bool, list[str]]:
    errors: list[str] = []
    required = ["open", "high", "low", "close"]
    if len(prediction) != horizon:
        errors.append(f"expected {horizon} rows, got {len(prediction)}")
    if any(column not in prediction for column in required):
        errors.append("missing OHLC columns")
        return False, errors
    values = prediction[required].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        errors.append("non-finite OHLC")
    if (values <= 0).any():
        errors.append("non-positive OHLC")
    valid_high = prediction["high"] >= prediction[["open", "close"]].max(axis=1)
    valid_low = prediction["low"] <= prediction[["open", "close"]].min(axis=1)
    if not bool((valid_high & valid_low).all()):
        errors.append("OHLC envelope violation")
    return not errors, errors


class KronosRuntime:
    def __init__(
        self,
        runtime_dir: Path = DEFAULT_RUNTIME,
        allow_download: bool = False,
        threads: int = 2,
        batch_size: int = 8,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.allow_download = allow_download
        self.batch_size = batch_size
        code_dir = runtime_dir / "Kronos"
        if not code_dir.exists():
            raise RuntimeError(f"Kronos source is missing: {code_dir}")
        sys.path.insert(0, str(code_dir))
        import torch
        from model import Kronos, KronosPredictor, KronosTokenizer

        random.seed(SEED)
        np.random.seed(SEED)
        torch.manual_seed(SEED)
        torch.use_deterministic_algorithms(True)
        torch.set_num_threads(max(1, threads))
        kwargs = {
            "cache_dir": str(runtime_dir / "hf"),
            "map_location": "cpu",
            "strict": True,
            "local_files_only": not allow_download,
        }
        tokenizer = KronosTokenizer.from_pretrained(
            TOKENIZER_ID, revision=TOKENIZER_REVISION, **kwargs
        )
        model = Kronos.from_pretrained(MODEL_ID, revision=MODEL_REVISION, **kwargs)
        tokenizer.eval()
        model.eval()
        self.torch = torch
        self.predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=2048)
        self.torch_version = torch.__version__

    def predict(
        self, frames: dict[str, pd.DataFrame], future_sessions: list[pd.Timestamp]
    ) -> dict[str, pd.DataFrame]:
        grouped: dict[int, list[str]] = defaultdict(list)
        for symbol, frame in frames.items():
            grouped[len(frame)].append(symbol)
        results: dict[str, pd.DataFrame] = {}
        for length in sorted(grouped, reverse=True):
            symbols = sorted(grouped[length])
            for start in range(0, len(symbols), self.batch_size):
                chunk = symbols[start : start + self.batch_size]
                data = [frames[symbol][["open", "high", "low", "close"]].reset_index(drop=True) for symbol in chunk]
                x_times = [pd.Series(frames[symbol].index).reset_index(drop=True) for symbol in chunk]
                y_times = [pd.Series(future_sessions) for _ in chunk]
                self.torch.manual_seed(SEED)
                with self.torch.inference_mode():
                    predictions = self.predictor.predict_batch(
                        data, x_times, y_times, pred_len=HORIZON,
                        T=1.0, top_k=1, top_p=1.0, sample_count=1, verbose=False,
                    )
                results.update(zip(chunk, predictions))
        return results


def item_from_prediction(
    symbol: str,
    name: str,
    frame: pd.DataFrame,
    prediction: pd.DataFrame,
    future_sessions: list[pd.Timestamp],
) -> dict[str, Any]:
    raw_valid, raw_errors = validate_raw_prediction(prediction)
    close = float(frame["close"].iloc[-1])
    steps = []
    for index, (_, row) in enumerate(prediction.iterrows(), start=1):
        steps.append({
            "session": index,
            "date": future_sessions[index - 1].date().isoformat(),
            "open": finite(row["open"]),
            "high": finite(row["high"]),
            "low": finite(row["low"]),
            "close": finite(row["close"]),
        })
    predicted_close = float(prediction["close"].iloc[-1])
    predicted_high = float(prediction["high"].max())
    predicted_low = float(prediction["low"].min())
    return {
        "symbol": symbol,
        "name": name,
        "as_of": frame.index[-1].date().isoformat(),
        "history_bars": len(frame),
        "close": finite(close),
        "steps": steps,
        "five_day": {
            "predicted_close": finite(predicted_close),
            "predicted_return_pct": finite((predicted_close / close - 1) * 100, 4),
            "path_high_pct": finite((predicted_high / close - 1) * 100, 4),
            "path_low_pct": finite((predicted_low / close - 1) * 100, 4),
        },
        "quality": {
            "raw_ohlc_valid": raw_valid,
            "raw_errors": raw_errors,
            "input_columns": ["open", "high", "low", "close"],
        },
    }


def append_history(path: Path, payload: dict[str, Any], keep: int = 500) -> None:
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    key = (payload["latest_trade_date"], payload["input_fingerprint"])
    records = [
        record for record in records
        if (record.get("latest_trade_date"), record.get("input_fingerprint")) != key
    ]
    records.append(payload)
    content = "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records[-keep:]) + "\n"
    atomic_write_text(path, content)


def validate_payload(payload: dict[str, Any], expected_symbols: int = EXPECTED_SYMBOLS) -> list[str]:
    errors: list[str] = []
    if payload.get("mode") != "shadow_research_only":
        errors.append("mode must be shadow_research_only")
    if payload.get("production_weights_changed") is not False or payload.get("formal_signal_logic_changed") is not False:
        errors.append("formal production flags must remain false")
    if payload.get("production_role") != "display_and_audit_only":
        errors.append("production_role must be display_and_audit_only")
    definition = payload.get("forecast_definition") or {}
    if definition.get("horizon_sessions") != HORIZON:
        errors.append("horizon_sessions must be 5")
    if len(definition.get("future_sessions") or []) != HORIZON:
        errors.append("future_sessions must contain 5 sessions")
    basis = payload.get("data_basis") or {}
    if basis.get("adjustment") != "qfq" or basis.get("is_final") is not True or basis.get("universe") != "formal_rotation":
        errors.append("data basis must remain final qfq formal_rotation")
    coverage = payload.get("coverage") or {}
    items = payload.get("items") or []
    if coverage.get("expected_symbols") != expected_symbols or coverage.get("predicted_symbols") != expected_symbols or len(items) != expected_symbols:
        errors.append("forecast coverage mismatch")
    symbols = [item.get("symbol") for item in items]
    if len(set(symbols)) != len(symbols):
        errors.append("duplicate symbols")
    for item in items:
        if item.get("as_of") != payload.get("latest_trade_date"):
            errors.append(f"stale item {item.get('symbol')}")
        if len(item.get("steps") or []) != HORIZON:
            errors.append(f"invalid horizon {item.get('symbol')}")
        five_day = item.get("five_day") or {}
        if any(not isinstance(five_day.get(field), (int, float)) or not math.isfinite(five_day[field]) for field in ("predicted_close", "predicted_return_pct", "path_high_pct", "path_low_pct")):
            errors.append(f"invalid five_day summary {item.get('symbol')}")
        for step in item.get("steps") or []:
            for field in ("open", "high", "low", "close"):
                value = step.get(field)
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    errors.append(f"non-finite {item.get('symbol')}.{field}")
    return errors


def generate(
    db_path: Path = DEFAULT_DB,
    out_path: Path = DEFAULT_OUT,
    history_path: Path = DEFAULT_HISTORY,
    runtime: Any | None = None,
    runtime_dir: Path = DEFAULT_RUNTIME,
    allow_download: bool = False,
    lookback: int = LOOKBACK,
    minimum_history: int = MINIMUM_HISTORY,
    expected_symbols: int = EXPECTED_SYMBOLS,
    universe: list[dict[str, str]] | None = None,
    future_sessions: list[pd.Timestamp] | None = None,
    reuse_cache: bool = True,
    batch_size: int = 8,
) -> dict[str, Any]:
    started = time.monotonic()
    universe = universe or rotation_universe()
    if len(universe) != expected_symbols:
        raise RuntimeError(f"formal rotation universe is {len(universe)}, expected {expected_symbols}")
    frames, names = load_frames(db_path, universe, lookback, minimum_history)
    missing = sorted(set(item["symbol"] for item in universe) - set(frames))
    if missing:
        raise RuntimeError(f"insufficient or invalid history for {len(missing)} symbols: {missing}")
    latest_trade_date = max(frame.index[-1].date() for frame in frames.values())
    stale = sorted(symbol for symbol, frame in frames.items() if frame.index[-1].date() != latest_trade_date)
    if stale:
        raise RuntimeError(f"stale symbol histories: {stale}")
    future_sessions = future_sessions or next_cn_sessions(latest_trade_date, HORIZON)
    parameters = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "code_revision": KRONOS_CODE_REVISION,
        "lookback": lookback,
        "minimum_history": minimum_history,
        "horizon_sessions": HORIZON,
        "seed": SEED,
        "T": 1.0,
        "top_k": 1,
        "top_p": 1.0,
        "sample_count": 1,
        "input_columns": ["open", "high", "low", "close"],
    }
    fingerprint = input_fingerprint(frames, future_sessions, parameters)
    if reuse_cache and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("input_fingerprint") == fingerprint and not validate_payload(existing, expected_symbols):
            return {**existing, "runtime": {**(existing.get("runtime") or {}), "cache_hit": True}}
    runtime = runtime or KronosRuntime(runtime_dir, allow_download=allow_download, batch_size=batch_size)
    predictions = runtime.predict(frames, future_sessions)
    if set(predictions) != set(frames):
        missing_predictions = sorted(set(frames) - set(predictions))
        raise RuntimeError(f"missing predictions: {missing_predictions}")
    items = [
        item_from_prediction(symbol, names.get(symbol, symbol), frames[symbol], predictions[symbol], future_sessions)
        for symbol in sorted(frames)
    ]
    returns = [item["five_day"]["predicted_return_pct"] for item in items]
    payload = {
        "schema_version": 1,
        "mode": "shadow_research_only",
        "model_family": "Kronos",
        "production_weights_changed": False,
        "formal_signal_logic_changed": False,
        "production_role": "display_and_audit_only",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_trade_date": latest_trade_date.isoformat(),
        "input_fingerprint": fingerprint,
        "data_basis": {
            "database": "data/local/etf-compass.db",
            "adjustment": "qfq",
            "is_final": True,
            "universe": "formal_rotation",
            "expected_symbols": expected_symbols,
        },
        "forecast_definition": {
            "target": "future_ohlc_path",
            "horizon_sessions": HORIZON,
            "future_sessions": [item.date().isoformat() for item in future_sessions],
            "interpretation": "deterministic greedy model path; research display only",
        },
        "model": {
            "checkpoint": MODEL_ID,
            "revision": MODEL_REVISION,
            "tokenizer": TOKENIZER_ID,
            "tokenizer_revision": TOKENIZER_REVISION,
            "code_revision": KRONOS_CODE_REVISION,
            "device": "cpu",
            "parameters": parameters,
        },
        "coverage": {
            "expected_symbols": expected_symbols,
            "predicted_symbols": len(items),
            "failed_symbols": [],
            "raw_ohlc_valid_symbols": sum(bool(item["quality"]["raw_ohlc_valid"]) for item in items),
            "minimum_history_bars": min(item["history_bars"] for item in items),
            "median_history_bars": int(np.median([item["history_bars"] for item in items])),
        },
        "summary": {
            "bullish_symbols": sum(value > 0 for value in returns),
            "bearish_symbols": sum(value < 0 for value in returns),
            "median_predicted_return_pct": finite(np.median(returns), 4),
            "mean_predicted_return_pct": finite(np.mean(returns), 4),
            "top_predicted": [
                {"symbol": item["symbol"], "name": item["name"], "predicted_return_pct": item["five_day"]["predicted_return_pct"]}
                for item in sorted(items, key=lambda row: row["five_day"]["predicted_return_pct"], reverse=True)[:10]
            ],
        },
        "validation": {
            "status": "shadow_accumulation",
            "formal_promotion_eligible": False,
            "required_checks": ["walk_forward_rank_ic", "directional_hit_rate", "max_drawdown", "turnover", "net_of_cost_increment"],
        },
        "runtime": {
            "seconds": finite(time.monotonic() - started, 3),
            "batch_size": batch_size,
            "cache_hit": False,
            "torch_version": getattr(runtime, "torch_version", "test-double"),
        },
        "items": items,
    }
    errors = validate_payload(payload, expected_symbols)
    if errors:
        raise RuntimeError("invalid Kronos payload: " + "; ".join(errors[:10]))
    append_history(history_path, payload)
    atomic_write_text(out_path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    try:
        payload = generate(
            db_path=args.db, out_path=args.out, history_path=args.history,
            runtime_dir=args.runtime, allow_download=args.download,
            reuse_cache=not args.no_cache, batch_size=args.batch_size,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "ok",
        "mode": payload["mode"],
        "trade_date": payload["latest_trade_date"],
        "universe": payload["coverage"]["predicted_symbols"],
        "horizon_sessions": payload["forecast_definition"]["horizon_sessions"],
        "cache_hit": payload["runtime"]["cache_hit"],
        "seconds": payload["runtime"].get("seconds"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
