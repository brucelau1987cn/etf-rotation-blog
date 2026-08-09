#!/usr/bin/env python3
"""Build, archive, and optionally publish the US ETF Compass weekly research page."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
LEARNING = DATA / "us-compass-learning.json"
SHADOW = DATA / "us-compass-shadow.json"
POOL = DATA / "us-etf-pool.json"
OUT = DATA / "us-compass-research.json"
CATALOG = DATA / "catalog.json"
IWENCAI_WRAPPER = Path("/root/.hermes/scripts/iwencai-skill-run")
PROJECT = "etf-rotation-blog"
if __package__:
    from .us_compass_fingerprint import consistent_model_fingerprint as _strict_consistent_fingerprint
else:
    fingerprint_path = Path(__file__).resolve().with_name("us_compass_fingerprint.py")
    fingerprint_spec = importlib.util.spec_from_file_location(
        f"_us_compass_fingerprint_{id(fingerprint_path)}", fingerprint_path
    )
    if fingerprint_spec is None or fingerprint_spec.loader is None:
        raise ImportError(f"cannot load model fingerprint from {fingerprint_path}")
    fingerprint_module = importlib.util.module_from_spec(fingerprint_spec)
    fingerprint_spec.loader.exec_module(fingerprint_module)
    _strict_consistent_fingerprint = fingerprint_module.consistent_model_fingerprint


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pct(value: Any) -> float | None:
    return round(float(value) * 100, 2) if value is not None else None


def week_key(value: str) -> str:
    parsed = date.fromisoformat(value[:10])
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def current_rows(pool: dict[str, Any], top_symbols: list[str]) -> list[dict[str, Any]]:
    by_symbol = {str(row.get("symbol") or ""): row for row in pool.get("rows", []) if row.get("symbol")}
    rows = []
    for symbol in top_symbols:
        row = by_symbol.get(symbol, {})
        rows.append({
            "symbol": symbol,
            "theme": row.get("theme") or row.get("asset_type") or "—",
            "trend_score": row.get("trend_score"),
            "risk_score": row.get("trading_risk_score"),
            "state": row.get("trade_state") or "—",
        })
    return rows


def consistent_model_fingerprint(learning: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    try:
        return _strict_consistent_fingerprint(learning, shadow)
    except ValueError as exc:
        if "mismatch" in str(exc):
            return {
                "status": "unavailable",
                "reason": "learning/shadow model fingerprint mismatch",
            }
        return {"status": "unavailable", "reason": "model fingerprint unavailable"}


def build_report(learning: dict[str, Any], shadow: dict[str, Any], pool: dict[str, Any], iwencai: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshots = learning.get("snapshots") or []
    latest = snapshots[-1] if snapshots else {}
    trade_date = str(pool.get("model_date") or latest.get("date") or "")
    if not trade_date:
        raise RuntimeError("US Compass research inputs have no trade date")
    metrics: dict[str, Any] = {}
    for key in ("t1", "t5", "t20"):
        raw = (learning.get("metrics") or {}).get(key) or {}
        reference = float(raw.get("random_deviation_reference") or 0.333333)
        deviation = raw.get("deviation_mean")
        metrics[key] = {
            "observations": int(raw.get("observations") or 0),
            "rank_ic_pct": pct(raw.get("rank_ic_mean")),
            "positive_rate_pct": pct(raw.get("rank_ic_positive_rate")),
            "deviation_pct": pct(deviation),
            "random_reference_pct": pct(reference),
            "deviation_vs_random_pp": round((float(deviation) - reference) * 100, 2) if deviation is not None else None,
        }
    stats = shadow.get("stats") or {}
    portfolios = {}
    for name in ("benchmark", "timing", "rotation", "fusion"):
        row = stats.get(name) or {}
        portfolios[name] = {
            "total_return_pct": pct(row.get("total_return")),
            "max_drawdown_pct": pct(row.get("max_drawdown")),
            "equity": row.get("equity"),
        }
    benchmark = float((stats.get("benchmark") or {}).get("total_return") or 0)
    timing = float((stats.get("timing") or {}).get("total_return") or 0)
    rotation = float((stats.get("rotation") or {}).get("total_return") or 0)
    fusion = float((stats.get("fusion") or {}).get("total_return") or 0)
    t5_observations = metrics["t5"]["observations"]
    raw_exposure = latest.get("exposure")
    exposure = float(raw_exposure) if isinstance(raw_exposure, (int, float, str)) else 0.5
    top_symbols = [str(item) for item in (latest.get("top10") or [])][:10]
    source = iwencai or {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    model_fingerprint = consistent_model_fingerprint(learning, shadow)
    return {
        "week_key": week_key(trade_date),
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_fingerprint": model_fingerprint,
        "verdict": "达到月度评估门槛" if t5_observations >= 20 else "样本积累中",
        "snapshot_count": len(snapshots),
        "metrics": metrics,
        "portfolios": portfolios,
        "attribution": {
            "timing_pp": round((timing - benchmark) * 100, 2),
            "rotation_pp": round((rotation - benchmark) * 100, 2),
            "interaction_pp": round((fusion - timing - rotation + benchmark) * 100, 2),
            "fusion_vs_benchmark_pp": round((fusion - benchmark) * 100, 2),
        },
        "risk_budget": {
            "current_exposure_pct": round(exposure * 100, 1),
            "regime": (pool.get("market_regime") or {}).get("state") or latest.get("regime") or "—",
            "allocation": (pool.get("market_regime") or {}).get("equity_allocation") or "—",
            "allowed_budgets_pct": [0, 50, 100],
        },
        "top10": current_rows(pool, top_symbols),
        "iwencai": {
            "status": source.get("status") or "unavailable",
            "source": "同花顺问财",
            "summary": source.get("summary") or "问财验证暂不可用",
        },
        "execution_basis": {
            "signal": "T日收盘生成",
            "execution": "T+1开盘执行",
            "rebalance": "次日开盘再平衡",
            "one_way_cost_pct": pct(shadow.get("one_way_cost")) or 0.1,
        },
        "production_change_allowed": False,
        "observations": [
            "T+5成熟样本达到20个前维持观察。" if t5_observations < 20 else "T+5样本已达到月度评估门槛，生产调整仍需人工授权。",
            "T+20无成熟样本时持续标记样本积累中。" if metrics["t20"]["observations"] == 0 else "继续观察T+20稳定性。",
            "周报仅记录研究证据，不自动修改权重或仓位阈值。",
        ],
    }


def merge_archive(existing: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    reports = [item for item in existing.get("reports", []) if isinstance(item, dict) and item.get("week_key") != report.get("week_key")]
    reports.append(report)
    reports.sort(key=lambda item: str(item.get("trade_date") or ""), reverse=True)
    return {
        "schema_version": "us-compass-research-v1",
        "updated_at": report.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "latest_week": report["week_key"],
        "reports": reports[:104],
    }


def iwencai_validation(pool: dict[str, Any]) -> dict[str, Any]:
    if not IWENCAI_WRAPPER.exists():
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    rows = sorted(pool.get("rows") or [], key=lambda row: float(row.get("trend_score") or 0), reverse=True)[:10]
    themes = []
    for row in rows:
        theme = str(row.get("theme") or "").strip()
        if theme and theme not in themes:
            themes.append(theme)
    query = "美股 " + " ".join(themes[:5]) + " 近20日涨幅、成交额、行业，按涨幅排序"
    cmd = [str(IWENCAI_WRAPPER), "hithink-usstock-selector", "-q", query, "--limit", "10", "--timeout", "45"]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    data = payload.get("datas") or []
    names = [str(item.get("股票简称") or item.get("股票名称") or item.get("股票代码") or "").strip() for item in data[:5]]
    names = [name for name in names if name]
    summary = f"问财查询“{query}”，返回{int(payload.get('code_count') or len(data))}条候选；前列样本：{'、'.join(names)}。" if names else f"问财查询“{query}”返回空结果，外部证据保持中性。"
    return {"status": "ok", "summary": summary, "source": "同花顺问财"}


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)


def publish() -> str:
    try:
        from pages_release import release_pages
    except ModuleNotFoundError:
        from scripts.pages_release import release_pages
    run(["python3", "scripts/generate_data_catalog.py"])
    run(["python3", "scripts/validate_public_data_contracts.py"])
    run(["npm", "run", "build"])
    run(["git", "add", "public/data/us-compass-research.json", "public/data/catalog.json"])
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
    if staged:
        run(["git", "commit", "-m", f"data: publish US Compass research {read_json(OUT).get('latest_week')}"])
        run(["git", "push", "origin", "HEAD:main"])
    return release_pages([
        "https://etf.peekabo.cc/us-compass/research/",
        "https://etf.peekabo.cc/data/us-compass-research.json",
    ], {
        "https://etf.peekabo.cc/data/us-compass-research.json": OUT,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iwencai", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    learning, shadow, pool = read_json(LEARNING), read_json(SHADOW), read_json(POOL)
    report = build_report(learning, shadow, pool, iwencai_validation(pool) if args.iwencai else None)
    archive = merge_archive(read_json(OUT, {"schema_version": "us-compass-research-v1", "reports": []}), report)
    atomic_write_json(OUT, archive)
    if args.publish:
        print(publish())
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
