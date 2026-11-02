#!/usr/bin/env python3
"""mootdx 通达信客户端（生产可用模块）。

上游 a-stock-data 2026-09 实测（#52）后语义已分裂：通达信公开服务器的**财务快照 / F10 / 除权除息仍正常**，
但 **K 线 / 五档盘口 / 逐笔自 2026-09 起普遍返回 0 行**（2026-11-02 本机 3 台节点复测一致：bars/quotes 0 行、
finance 1 行、F10 仅「最新提示」）。所以验活必须区分用途，用 K 线验财务会把所有正常节点判死：

    client = tdx_client(check='finance')   # 财务快照 / F10 —— 可用
    client = tdx_client()                  # K 线 / 盘口 / 逐笔 —— 目前普遍取不到，price 走腾讯

价格与 K 线一律以腾讯为主源（`fetch_*` 系列，前复权），本模块只在其之外提供财务与 F10 的免费补充。

来源：https://github.com/simonlin1212/a-stock-data（Apache 2.0）tdx_client 逻辑（V3.10.3），整合为项目正式模块。

运行时：/root/.cache/mootdx/venv（独立 venv，mootdx 锁 httpx<0.26，避免污染项目主 venv）。
用法：
  /root/.cache/mootdx/venv/bin/python scripts/mootdx_quote.py 600519
  /root/.cache/mootdx/venv/bin/python scripts/mootdx_quote.py 600519 --check finance
"""
from __future__ import annotations

import socket

from mootdx.quotes import Quotes

_TDX_SERVERS = [
    ('119.97.185.59', 7709), ('124.70.133.119', 7709), ('116.205.183.150', 7709),
    ('123.60.73.44', 7709),  ('116.205.163.254', 7709), ('121.36.225.169', 7709),
    ('123.60.70.228', 7709), ('124.71.9.153', 7709),    ('110.41.147.114', 7709),
    ('124.71.187.122', 7709),
]

# 行情类命令失效后的替代源（上游 §1.2 / §1.3 / §1.4）
BARS_UNAVAILABLE_HINT = (
    "2026-09 起通达信公开服务器的 K 线 / 盘口 / 逐笔普遍返回空表（上游 #52）："
    "K 线改用腾讯前复权日K，当日逐笔改用腾讯分笔，财务与 F10 仍可用 tdx_client(check='finance')。"
)


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手粗筛。握手成功不代表能取数，必须再经 _validate。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _validate(client, market: str = 'std', check: str = 'bars') -> bool:
    """真实取数验活。坏节点可握手通过却回空 body，只有真取一次数才算活。

    check='bars'   用日线（frequency=4；9 是兼容别名）验活，供 K 线/盘口/逐笔调用；
    check='finance' 要求 F10 类别表含「最新提示」且正文非空、财务快照非空。
                   只验财务会选中「财务正常、F10 为空」的节点，反之亦然。
    非 std 市场（如扩展行情 'ext'）用 000001 必然取不到数，会把正常节点全判死，故跳过验活。
    """
    if market != 'std':
        return True
    try:
        if check == 'finance':
            cats = client.F10C(symbol='000001')
            if not any(isinstance(c, dict) and c.get('name') == '最新提示' for c in (cats or [])):
                return False
            text = client.F10(symbol='000001', name='最新提示')
            if not isinstance(text, str) or not text.strip():
                return False
            df = client.finance(symbol='000001')
        else:
            df = client.bars(symbol='000001', frequency=4, offset=1)
        return df is not None and not df.empty
    except Exception:
        return False


def tdx_client(market: str = 'std', check: str = 'bars'):
    """创建 mootdx 客户端：显式 server 绕开 BESTIP 空串 bug + 逐台真实取数验活 + bestip/裸 factory 回退。

    check: 'bars'（默认，K 线 / 盘口 / 逐笔）或 'finance'（财务快照 / F10）。
    """
    if check not in ('bars', 'finance'):
        raise ValueError("check 只能是 'bars' 或 'finance'")
    for ip, port in _TDX_SERVERS:
        if not _probe(ip, port):
            continue
        try:
            c = Quotes.factory(market=market, server=(ip, port))
            if _validate(c, market, check):
                return c
        except Exception:
            continue
    for kwargs in ({'bestip': True}, {}):
        try:
            c = Quotes.factory(market=market, **kwargs)
            if _validate(c, market, check):
                return c
        except Exception:
            continue
    hint = "海外网络通常全部超时（TCP 7709），请走国内代理或更新 _TDX_SERVERS。"
    if check == 'bars':
        hint += BARS_UNAVAILABLE_HINT
    raise RuntimeError("所有 mootdx 服务器均无法取到数据（TCP 可达但返回空 / 被 reset）。" + hint)


def get_kline(code: str, freq: int = 4, count: int = 10):
    """K线。freq: 0=5分 1=15分 2=30分 3=60分 4=日 5=周 6=月 8=1分 9=日(兼容别名)。

    ⚠️ 行情类命令 2026-09 起普遍返回空表，需要价格请走腾讯主源。
    """
    client = tdx_client()
    return client.bars(symbol=code, frequency=freq, offset=count)


def get_quote(codes: list[str]):
    """实时报价（46 字段）。⚠️ 同上：行情类命令目前普遍返回空表。"""
    client = tdx_client()
    return client.quotes(symbol=codes)


def get_finance(code: str):
    """财务快照（mootdx finance，仍可用）。"""
    client = tdx_client(check='finance')
    return client.finance(symbol=code)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="mootdx 通达信数据")
    parser.add_argument("code", help="6 位代码")
    parser.add_argument("--check", choices=("bars", "finance"), default="bars")
    parser.add_argument("--bars", type=int, default=5)
    parser.add_argument("--freq", type=int, default=4)
    args = parser.parse_args()

    if args.check == 'finance':
        df = get_finance(args.code)
        print(f"=== {args.code} 财务快照 ===")
        print(df.to_string() if df is not None and not df.empty else "(空)")
        return 0

    bars = get_kline(args.code, args.freq, args.bars)
    print(f"=== {args.code} K线 ===")
    print(bars[['open', 'close', 'high', 'low', 'vol']].to_string() if bars is not None and not bars.empty else "(空)")

    q = get_quote([args.code])
    print(f"\n=== {args.code} 报价 ===")
    if q is not None and not q.empty:
        for c in ('price', 'open', 'high', 'low', 'last_close', 'vol', 'amount', 'servertime'):
            if c in q.columns:
                print(f"  {c}: {q[c].iloc[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
