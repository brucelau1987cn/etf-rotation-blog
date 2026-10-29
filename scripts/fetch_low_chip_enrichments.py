#!/usr/bin/env python3
"""Fetch profile, individual, and quality shareholder queries for low-chip stocks.

耗时说明：本脚本是整条低筹码发布链里最慢的一环。成本主要来自两处：

1. iWenCai 分批查询（profile + individual）。每批 5 只，442 只 = 89+89 = 178 次
   调用。串行执行时实测每次 15-21s，合计约 54 分钟，必然撞穿外层预算。
   这里改成并发执行（实测 4 并发稳定，单次 15s 墙钟内返回全部 4 个结果）。
2. iWenCai 逐只 fallback 链。批量响应经常缺少「前十大流通股东名称」聚合字段，
   缺字段的股票会逐只回退查询，每只最多 3 次调用。FTShare 一次查询即可覆盖全部
   十大流通股东，是首选来源。

FTShare 匿名额度为 500 次/天；带 API Key（/root/.hermes/credentials/ftshare-api.env，
FTSHARE_API_KEYS 逗号分隔）不受匿名额度限制。额度一旦耗尽，stock_float_holders 会
持续返回 HTTP 429，因此 fetch_ftshare_holders 在检测到 429 时会立刻 fail-closed 中止，
避免把整个股票池推进 iWenCai fallback 白跑几个小时。

关键：FTShare 子进程的超时必须随股票池规模增长。硬编码 900s 在 442 只时
（实测 1.64s/只 ≈ 725s）会踩线超时，异常被 except 吞掉后返回空 dict，
结果 442 只全部涌入逐只 fallback 链——这正是 2026-10-29 enrichment 5400s
超时的放大机制。现在按 4s/只 + 120s 余量动态计算。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

PROFILE_BATCH_SIZE = 5  # iWenCai batch size per /tmp/lc_p{n}.json
# 并发度：iWenCai 实测 4 并发无 429；保守取 4，避免触发风控。
IWENCAI_WORKERS = int(os.environ.get("LOW_CHIP_IWENCAI_WORKERS", "4"))
# FTShare 子进程超时：按每只 4s + 120s 余量随池规模增长。
FTSHARE_SECONDS_PER_CODE = 4.0
FTSHARE_TIMEOUT_FLOOR = 900
SHAREHOLDER_PERIOD_RE = re.compile(r"^前十大流通股东名称(?:\(报告期\))?\[(\d{8})\]$")

QUALITY_QUERY = " 前十大流通股东名称包含全国社保基金或基本养老保险基金或国家集成电路产业投资基金或国新投资或深圳市创新投资集团或科威特政府投资局或澳门金融管理局"


def iwc(query, page=1, limit=50, timeout=90):
    last_err = ""
    for attempt in range(3):
        r = subprocess.run(
            ["/root/.hermes/scripts/iwencai-market-query", "-q", query, "--page", str(page),
             "--limit", str(limit), "--timeout", str(timeout)],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError as exc:
                last_err = f"JSON decode error: {exc}"
                time.sleep(5 * (attempt + 1))
                continue
        # quota 耗尽不可恢复，立即失败（不重试）；瞬时失败（超时/服务端）重试。
        if any(x in (r.stdout or "") for x in ("已达上限", "次数已用完", "额度已用完")):
            raise SystemExit(f"iWenCai quota exhausted: {(r.stdout or '')[:200]}")
        last_err = (r.stderr or r.stdout or "")[:200]
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"iWenCai error after retries: {last_err}")


FTSHARE_PY = "/root/.cache/ftshare-sdk/bin/python"
FTSHARE_SDK_URL = "https://market.ft.tech/gateway/"
FTSHARE_ENV_FILE = Path("/root/.hermes/credentials/ftshare-api.env")


def _read_ftshare_keys_file() -> str:
    """从 host 凭据文件读 FTSHARE_API_KEYS；文件不可读时视为未配置。

    非 root 用户 / CI / 沙箱里 `/root/...` 连 exists() 都会抛 PermissionError，
    所以这里只能吞 OSError，绝不能让它冒泡成调用方的致命错误。
    """
    try:
        content = FTSHARE_ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.startswith("FTSHARE_API_KEYS="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def ftshare_api_keys() -> list[str]:
    """读取 FTShare API Keys（多 key，逗号分隔）。

    带 key 访问不受 500 次/天的匿名额度限制。无 key 时走匿名访问，
    此时 fetch_ftshare_holders 会在撞上匿名额度后 fail-closed。
    """
    raw = os.environ.get("FTSHARE_API_KEYS") or ""
    if not raw:
        raw = _read_ftshare_keys_file()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    # 去重且保持顺序
    seen = set()
    unique = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def fetch_ftshare_holders(codes: list[str]) -> dict[str, dict]:
    """用 FTShare SDK 查十大流通股东，构建 iWenCai 兼容字段。

    返回 {bare_code: {"前十大流通股东名称(报告期)[YYYYMMDD]": "name1, name2, ..."}}；
    失败返回空 dict（调用方 fallback iWenCai）。

    FTShare 有两条重要的失败语义，必须分开处理：

    * 匿名额度耗尽（HTTP 429 / "最多访问 500 次"）——继续重试只会延长封禁，
      且后续每只都会失败。此时必须立刻中止并在 stdout 报告 blocked 状态，
      让上游决定是否 STAGING BLOCKER，而不是静默跳过全部股票。
    * 单只股票确实无十大流通股东数据——属于正常业务情况，跳过即可。

    历史上 `except Exception: pass` 把两者混为一谈，导致限额耗尽后 198 只全部
    落入 iWenCai 逐只 fallback 链，跑成 146 分钟且最终丢失 13 只的股东 badge。
    """
    if not codes:
        return {}
    api_keys = ftshare_api_keys()
    # 超时必须随池规模增长。实测 1.64s/只：442 只 ≈ 725s，硬编码 900s 必然踩线。
    # 踩线后的异常被 except Exception 吞掉、返回空 dict，会把 442 只全部推进
    # iWenCai 逐只回退链（每只最多 3 次调用），这正是 2026-10-29 enrichment
    # 5400s 超时的放大机制。按 4s/只 + 120s 余量给足空间。
    ftshare_timeout = max(
        FTSHARE_TIMEOUT_FLOOR,
        int(len(codes) * FTSHARE_SECONDS_PER_CODE) + 120,
    )
    inline = r'''
import json, sys, time
from ftshare.client import FtshareClient
codes = json.loads(sys.stdin.read())
# __KEYS__ 被外层替换成一个 JSON 数组字面量（如 ["k1","k2"]），直接求值即可
keys = __KEYS__
out = {}
empty = []
errors = []
switches = 0
key_index = 0

def make_client():
    if keys:
        return FtshareClient(base_url="__URL__", timeout=60, headers={"FTSHARE_API_KEY": keys[key_index]})
    return FtshareClient(base_url="__URL__", timeout=60)

client = make_client()
for code in codes:
    try:
        rows = client.stock_float_holders(stock_code=code, all_pages=False, page_size=20, raw=True)
        items = ((rows or {}).get("data") or {}).get("items") or []
        if not items:
            empty.append(code)
            continue
        item = items[0]
        period = str(item.get("publish_date") or "").replace("-", "")
        holders = item.get("fen_holders") or []
        names = [str(h.get("shareholder_name") or "").strip() for h in holders if str(h.get("shareholder_name") or "").strip()]
        if names and len(period) == 8:
            out[code.split(".")[0]] = {"前十大流通股东名称(报告期)[" + period + "]": ", ".join(names)}
        else:
            empty.append(code)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        rate_limited = ("429" in msg or "最多访问" in msg or "rate_limited" in msg)
        key_malformed = ("401" in msg or "api_key_malformed" in msg)
        # 单个 key 限流/损坏且还有备用 key：切换后重试同一只，不丢数据
        if (rate_limited or key_malformed) and keys and key_index + 1 < len(keys):
            key_index += 1
            switches += 1
            client = make_client()
            try:
                rows = client.stock_float_holders(stock_code=code, all_pages=False, page_size=20, raw=True)
                items = ((rows or {}).get("data") or {}).get("items") or []
                if items:
                    item = items[0]
                    period = str(item.get("publish_date") or "").replace("-", "")
                    holders = item.get("fen_holders") or []
                    names = [str(h.get("shareholder_name") or "").strip() for h in holders if str(h.get("shareholder_name") or "").strip()]
                    if names and len(period) == 8:
                        out[code.split(".")[0]] = {"前十大流通股东名称(报告期)[" + period + "]": ", ".join(names)}
                        continue
            except Exception as exc2:
                msg = f"{type(exc2).__name__}: {exc2}"
            empty.append(code)
            continue
        # 无备用 key 可切：额度耗尽立刻中止，不要再消耗封禁时间
        if rate_limited:
            print(json.dumps({
                "blocked": True, "reason": "ftshare_anonymous_rate_limited" if not keys else "ftshare_rate_limited",
                "detail": msg[:300], "holders": out, "empty": empty, "missing_after_block": len(codes) - len(out) - len(empty),
                "keys_available": len(keys), "switches": switches,
            }, ensure_ascii=False))
            sys.exit(0)
        errors.append({"code": code, "error": msg[:200]})
    time.sleep(0.15)
print(json.dumps({"holders": out, "empty": empty, "errors": errors, "keys_available": len(keys), "switches": switches}, ensure_ascii=False))
'''.replace("__URL__", FTSHARE_SDK_URL).replace("__KEYS__", json.dumps(api_keys))
    try:
        r = subprocess.run(
            [FTSHARE_PY, "-c", inline],
            input=json.dumps(codes, ensure_ascii=False),
            capture_output=True, text=True, check=False, timeout=ftshare_timeout,
        )
        if r.returncode != 0:
            print(f"WARNING: FTShare holders query failed: {r.stderr[:200]}", file=sys.stderr)
            return {}
        payload = json.loads(r.stdout)
        if payload.get("blocked"):
            print(
                f"STAGING BLOCKER: FTShare 匿名额度耗尽，无法获取 {payload.get('missing_after_block')} 只流通股东："
                f"{payload.get('detail','')[:200]}",
                file=sys.stderr,
            )
            raise SystemExit(
                "FTShare anonymous quota exhausted; register an API key at https://ftai.chat/ftshare "
                f"(missing after block: {payload.get('missing_after_block')} stocks)"
            )
        holders = payload.get("holders") or {}
        for err in payload.get("errors") or []:
            print(f"  {err.get('code')}: ftshare holder query error; {err.get('error','')[:120]}", flush=True)
        return holders
    except SystemExit:
        raise
    except subprocess.TimeoutExpired:
        # 超时与"额度耗尽"是两种不同的失败：额度耗尽已在上游 fail-closed；
        # 超时意味着这批规模本身跑不完。此时必须显式失败，不能返回空 dict，
        # 否则整池会涌入 iWenCai 逐只回退链把预算烧光（2026-10-29 的放大机制）。
        raise SystemExit(
            f"FTShare holders query timed out after {ftshare_timeout}s for {len(codes)} stocks "
            f"(budget scales at {FTSHARE_SECONDS_PER_CODE}s/code); refusing to fall back to "
            f"per-stock iWenCai queries for the whole pool"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: FTShare holders query failed: {exc}", file=sys.stderr)
        return {}


def has_top10_names(row: dict | None) -> bool:
    return bool(row and any(
        key.startswith("前十大流通股东名称") and value
        for key, value in row.items()
    ))


def report_period_from_rows(rows: list[dict]) -> str:
    periods = []
    for row in rows:
        for key in row:
            match = SHAREHOLDER_PERIOD_RE.match(key)
            if match:
                periods.append(match.group(1))
            key_period = re.search(r"(?:报告期|截止日期)\[(\d{8})\]", key)
            if key_period:
                periods.append(key_period.group(1))
        for key in ("截止日期", "报告期"):
            value = str(row.get(key) or "")
            digits = re.sub(r"\D", "", value)
            if len(digits) >= 8:
                periods.append(digits[:8])
    return max(periods) if periods else ""


def aggregate_top10_detail(code: str, rows: list[dict], fallback_period: str = "") -> dict | None:
    """Build one current top-10 evidence row from iWenCai detail rows.

    The detail engine currently returns one holder per row under `名称`, while
    older responses used `流通股东名称`. Historical exits marked `新出` lack
    current rank/announcement evidence and are deliberately excluded.
    """
    bare = code.split(".")[0]
    selected = []
    for row in rows:
        if str(row.get("股票代码") or "").upper().split(".")[0] != bare:
            continue
        if row.get("排名") is None or not (row.get("公告日期") or any(str(k).startswith("公告日期[") for k in row)):
            continue
        name = str(row.get("流通股东名称") or row.get("股东名称") or row.get("名称") or "").strip()
        if name and name not in selected:
            selected.append(name)
    period = report_period_from_rows(rows) or fallback_period
    if not selected or not period:
        return None
    return {"股票代码": code, f"前十大流通股东名称(报告期)[{period}]": ", ".join(selected)}


def select_latest_top10_report_row(code: str, rows: list[dict]) -> dict | None:
    """Select the latest explicitly dated consolidated top-10 holder field."""
    bare = code.split(".")[0]
    candidates: list[tuple[str, str, object]] = []
    for row in rows:
        if str(row.get("股票代码") or "").upper().split(".")[0] != bare:
            continue
        for key, value in row.items():
            match = SHAREHOLDER_PERIOD_RE.match(key)
            if match and value:
                candidates.append((match.group(1), key, value))
    if not candidates:
        return None
    _, key, value = max(candidates, key=lambda item: item[0])
    return {"股票代码": code, key: value}


def enrichment_codes(payload: dict) -> list[str]:
    """Return the complete non-BJ set that enrich_low_chip_stocks will rebuild.

    A published payload's ``intersection`` may already be narrowed by downstream
    filters. Querying that subset leaves missing profile rows when enrichment is
    rebuilt from ``intersection_before_filters`` during a skip-build recovery.
    """
    raw = payload.get("intersection_before_filters")
    candidates = raw if isinstance(raw, list) else (payload.get("intersection") or [])
    seen: set[str] = set()
    result: list[str] = []
    for value in candidates:
        code = str(value or "").strip()
        if not code or code.endswith(".BJ") or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def main() -> int:
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    codes = enrichment_codes(payload)
    bare_codes = [c.split(".")[0] for c in codes]
    print(f"intersection: {codes}", flush=True)

    # 清理旧的 profile 分页文件，避免 enrich_low_chip_stocks.py 的 glob 读到历史残留。
    for stale in Path("/tmp").glob("lc_p*.json"):
        stale.unlink(missing_ok=True)

    # /tmp/lc_p{n}.json — per-stock profile query (industry, top10, shareholders)
    # 5 codes per batch. Batches run concurrently: 442 stocks = 89 batches, and
    # each call measures 15-21s. Serial execution burns ~54 minutes on the two
    # batch stages alone, which is what exhausted the enrichment budget on
    # 2026-10-29. iWenCai handles 4 concurrent queries without 429.
    batches = [bare_codes[i:i + PROFILE_BATCH_SIZE] for i in range(0, len(bare_codes), PROFILE_BATCH_SIZE)]
    if not batches:
        batches = [[]]

    def fetch_profile_batch(item: tuple[int, list[str]]) -> tuple[int, dict]:
        idx, batch = item
        q = "、".join(batch) + " 所属申万行业、最新报告期前十大流通股东名称、最新报告期流通股东名称、流通股东类型、持股数量、持股比例、流通股东持股变动类型、公告日期、所属概念"
        return idx, iwc(q)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=IWENCAI_WORKERS) as pool:
        for idx, d in pool.map(fetch_profile_batch, list(enumerate(batches, start=1))):
            out = Path(f"/tmp/lc_p{idx}.json")
            out.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            print(f"  lc_p{idx}: rows={len(d.get('datas') or [])}", flush=True)
    print(f"  profile batches: {len(batches)} in {time.monotonic() - started:.1f}s "
          f"(workers={IWENCAI_WORKERS})", flush=True)

    # /tmp/low_chip_individual.json — per-stock current shareholding row with industry/concept
    rows = []
    INDIV_BATCH = 5
    batch_cache: dict[str, dict] = {}

    def fetch_individual_batch(batch: list[str]) -> dict[str, dict]:
        bare_batch = [c.split(".")[0] for c in batch]
        q = "、".join(bare_batch) + " 最新价、最新涨跌幅、所属申万行业、所属概念、最新完整报告期十大流通股东明细、前十大流通股东名称、股东名称、股东类型、持股比例、排名、持股变动类型、公告日期、上市地点、所属同花顺行业、上市板块"
        d = iwc(q, limit=max(50, len(batch) * 20))
        # Each batch owns its codes exclusively, so the local dict needs no lock:
        # a stock code appears in exactly one batch, and the merge below happens
        # after the pool has joined.
        local: dict[str, dict] = {}
        for r in d.get("datas") or []:
            row_code = str(r.get("股票代码", "")).upper().split(".")[0]
            if row_code in bare_batch and row_code not in local:
                local[row_code] = r
        return local

    indiv_batches = [codes[i:i + INDIV_BATCH] for i in range(0, len(codes), INDIV_BATCH)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=IWENCAI_WORKERS) as pool:
        for local in pool.map(fetch_individual_batch, indiv_batches):
            batch_cache.update(local)
    print(f"  individual batches: {len(indiv_batches)} in {time.monotonic() - started:.1f}s "
          f"(workers={IWENCAI_WORKERS}, cached={len(batch_cache)})", flush=True)
    ftshare_holders = fetch_ftshare_holders(codes)
    for code in codes:
        matched = batch_cache.get(code.split(".")[0])
        # Batch-style shareholder fields can return an incomplete row set.
        # Retry the industry/quote identity query per symbol before fail-closed validation.
        if matched is None or not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
            fallback = iwc(
                f"{code.split('.')[0]} 所属申万行业、所属同花顺行业、股票简称、最新价、最新涨跌幅",
                limit=10,
            )
            fallback_rows = fallback.get("datas") or []
            for r in fallback_rows:
                row_code = str(r.get("股票代码", "")).upper().split(".")[0]
                if row_code == code.split(".")[0]:
                    if matched is None:
                        matched = r
                    elif not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
                        matched["所属申万行业"] = r.get("所属申万行业")
                        matched["所属同花顺行业"] = r.get("所属同花顺行业")
                    break
            if matched is None or not (matched.get("所属申万行业") or matched.get("所属同花顺行业")):
                # 行业缺失不再中断整条发布：该股 enrich 阶段标「待补充」，
                # 页面显示「—」。keep a light record, keep going.
                print(
                    f"  {code}: industry unavailable; enrich marks 待补充 "
                    f"(returned={len(fallback_rows)})",
                    flush=True,
                )
                if matched is None:
                    matched = {"股票代码": code}
        if not has_top10_names(matched):
            fts = ftshare_holders.get(code.split(".")[0])
            if fts:
                # FTShare 已拿到完整十大流通股东明细，直接填充，省 iWenCai fallback 链
                if matched is None:
                    matched = fts
                else:
                    for key, value in fts.items():
                        if key.startswith("前十大流通股东名称") and value:
                            matched[key] = value
            else:
                holder_detail = iwc(
                    f"{code.split('.')[0]} 最新完整报告期十大流通股东明细、前十大流通股东名称、公告日期",
                    limit=30,
                )
                detail_rows = holder_detail.get("datas") or []
                detail = next((
                    r for r in detail_rows
                    if str(r.get("股票代码") or "").upper().split(".")[0] == code.split(".")[0]
                    and has_top10_names(r)
                ), None)
                if detail is None:
                    detail = aggregate_top10_detail(
                        code, detail_rows, report_period_from_rows([matched] if matched else [])
                    )
                if detail is None:
                    period_payload = iwc(
                        f"{code.split('.')[0]} 前十大流通股东报告期、截止日期",
                        limit=10,
                    )
                    detail = select_latest_top10_report_row(
                        code, period_payload.get("datas") or []
                    )
                if detail is None:
                    period = report_period_from_rows(detail_rows) or report_period_from_rows([matched] if matched else [])
                    query = f"{code.split('.')[0]} 前十大流通股东名称" + (f"[{period}]" if period else "")
                    holder_names = iwc(query, limit=20)
                    name_rows = holder_names.get("datas") or []
                    names = []
                    for item in name_rows:
                        if str(item.get("股票代码") or "").upper().split(".")[0] != code.split(".")[0]:
                            continue
                        name = str(item.get("流通股东名称") or item.get("股东名称") or item.get("名称") or "").strip()
                        if name and name not in names:
                            names.append(name)
                    if names:
                        if not period:
                            # 股东名称已拿到但报告期缺失：不再中断整条发布，
                            # 该股 badge 默认 false（evidence fail-closed），继续下一只。
                            print(
                                f"  {code}: shareholder names without report period; "
                                f"badge defaults to false",
                                flush=True,
                            )
                            detail = None
                        else:
                            detail = {"股票代码": code, f"前十大流通股东名称(报告期)[{period}]": ", ".join(names)}
                if detail is None:
                    # Some stocks have no currently published top-10 holder names.
                    # Shareholder badges are evidence-based and therefore fail closed;
                    # industry/concept enrichment remains valid and must continue.
                    print(
                        f"  {code}: top10 shareholder names unavailable; "
                        f"badges default to false (returned={len(detail_rows)})",
                        flush=True,
                    )
                if matched is None:
                    matched = detail
                elif detail is not None:
                    for key, value in detail.items():
                        if key.startswith("前十大流通股东名称") and value:
                            matched[key] = value
        if matched is not None:
            rows.append(matched)
    Path("/tmp/low_chip_individual.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"  individual rows: {len(rows)}", flush=True)

    # /tmp/low_chip_quality.json — quality shareholders check
    q = "、".join(bare_codes) + QUALITY_QUERY
    d = iwc(q, limit=max(20, len(bare_codes) * 3))
    Path("/tmp/low_chip_quality.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"  quality rows: {len(d.get('datas') or [])}", flush=True)

    # 财务指标已改为 Fuyao 接口（attach_low_chip_financials.py 直接查 Fuyao），
    # 此处不再用 iWenCai 查财务（省 1 次配额）。

    return 0


if __name__ == "__main__":
    raise SystemExit(main())