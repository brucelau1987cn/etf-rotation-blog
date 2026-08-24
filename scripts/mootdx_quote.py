#!/usr/bin/env python3
"""mootdx 通达信行情客户端（生产可用模块）。

作为腾讯行情之外的免费补充源：K线(多周期)/五档盘口/逐笔/实时报价 46 字段，TCP 7709 不封 IP。
规避 mootdx 0.11.x BESTIP 空串 bug + 坏服务器静默空表（TCP 握手成功 ≠ 能取数）。

来源：https://github.com/simonlin1212/a-stock-data（Apache 2.0）tdx_client 逻辑，整合为项目正式模块。

运行时：/root/.cache/mootdx/venv（独立 venv，mootdx 锁 httpx<0.26，避免污染项目主 venv）。
用法：
  /root/.cache/mootdx/venv/bin/python scripts/mootdx_quote.py 600519
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


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _validate(client, market: str = 'std') -> bool:
    if market != 'std':
        return True
    try:
        df = client.bars(symbol='000001', frequency=9, offset=1)
        return df is not None and not df.empty
    except Exception:
        return False


def tdx_client(market: str = 'std'):
    """创建 mootdx 客户端（BESTIP 规避 + 逐台真实取数验活 + bestip/裸 factory 回退）。"""
    for ip, port in _TDX_SERVERS:
        if not _probe(ip, port):
            continue
        try:
            c = Quotes.factory(market=market, server=(ip, port))
            if _validate(c, market):
                return c
        except Exception:
            continue
    for kwargs in ({'bestip': True}, {}):
        try:
            c = Quotes.factory(market=market, **kwargs)
            if _validate(c, market):
                return c
        except Exception:
            continue
    raise RuntimeError("所有 mootdx 服务器均无法取到数据（TCP 可达但返回空 / 被 reset）。")


def get_kline(code: str, freq: int = 9, count: int = 10):
    """日线/分钟 K线。freq: 0=5分 1=15分 2=30分 3=60分 4=日 5=周 6=月 8=1分 9=日(默认)。"""
    client = tdx_client()
    return client.bars(symbol=code, frequency=freq, offset=count)


def get_quote(codes: list[str]):
    """实时报价（46 字段）。返回 DataFrame。"""
    client = tdx_client()
    return client.quotes(symbol=codes)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="mootdx 通达信行情")
    parser.add_argument("code", help="6 位代码")
    parser.add_argument("--bars", type=int, default=5)
    parser.add_argument("--freq", type=int, default=9)
    args = parser.parse_args()

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
