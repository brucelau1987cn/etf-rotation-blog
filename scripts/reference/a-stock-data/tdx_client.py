#!/usr/bin/env python3
"""mootdx（通达信 TCP）客户端 — 规避 0.11.x BESTIP 空串 bug + 坏服务器静默空表。

来源：https://github.com/simonlin1212/a-stock-data（SKILL.md §Prerequisites，V3.10.3）
License：Apache 2.0（原项目），抽取整合为自包含脚本，供参考/对比，未接入生产。

为何要这个 helper：mootdx 0.11.x 全新安装后 `Quotes.factory(market='std')` 裸调用可能抛
`ValueError: not enough values to unpack`——根因 `~/.mootdx/config.json` 的 BESTIP.HQ 是空串。
且「TCP 握手成功 ≠ 能取数」：坏服务器可握手通过却回 2 字节空 body → 静默空表。
故逐台「真实取数验活」，再回退 bestip 测速 / 裸 factory。

⚠️ 验活必须分用途（上游 #52，2026-09 起）：通达信公开服务器的 K 线 / 盘口 / 逐笔普遍返回空表，
而财务快照 / F10 / 除权除息仍正常。用 K 线验财务会把所有正常节点判死，故本实现提供 check 参数。
本机 2026-11-02 复测 3 台节点：bars 0 行 / quotes 0 行 / finance 1 行 / F10C 仅「最新提示」。

用法：
  python3 tdx_client.py 600519                    # K线 + 报价（目前普遍取不到，见上）
  python3 tdx_client.py 600519 --check finance    # 财务快照（可用）

依赖：mootdx（pip install mootdx；其锁 httpx<0.26 但取行情走 TCP 不经 httpx，可用
      `pip install --no-deps "httpx>=0.27.1"` 与 MCP 共存，或独立 venv 隔离）
"""
from __future__ import annotations

import argparse
import socket

from mootdx.quotes import Quotes

# 实测可用的备选服务器（按延迟排序，2026-06 验证）
_TDX_SERVERS = [
    ('119.97.185.59', 7709), ('124.70.133.119', 7709), ('116.205.183.150', 7709),
    ('123.60.73.44', 7709),  ('116.205.163.254', 7709), ('121.36.225.169', 7709),
    ('123.60.70.228', 7709), ('124.71.9.153', 7709),    ('110.41.147.114', 7709),
    ('124.71.187.122', 7709),
]


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测（快速粗筛）。握手成功 ≠ 能取数，必须再经 _validate 验活。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _validate(client, market: str = 'std', check: str = 'bars') -> bool:
    """真实取数验活（坏服务器可 TCP 握手通过却回空 body）。

    check='bars'    ：用日线验活（frequency=4；9 是兼容别名）；
    check='finance' ：财务快照 + F10「最新提示」类别表与正文都要取到才算活——
                      只验财务会选中「财务正常、F10 为空」的节点，F10 随后静默给空文本。
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
    """创建 mootdx 客户端，规避 BESTIP 空串 bug + 坏服务器静默空表。

    check: 'bars'（默认，K 线 / 盘口 / 逐笔）或 'finance'（财务快照 / F10）。
    顺序：逐台显式 server 验活 → bestip 测速 → 裸 factory → 全部失败抛 RuntimeError。
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
    hint = "海外网络通常全部超时（TCP 7709），请走国内代理或更新 _TDX_SERVERS 列表。"
    if check == 'bars':
        hint += ("2026-09 起通达信公开服务器的 K 线 / 盘口 / 逐笔普遍返回空表（#52）："
                 "K 线改用腾讯前复权日K，当日逐笔改用腾讯分笔，财务与 F10 用 check='finance' 仍可取。")
    raise RuntimeError("所有 mootdx 服务器均无法取到数据（TCP 可达但返回空 / 被 reset）。" + hint)


def main() -> int:
    parser = argparse.ArgumentParser(description="mootdx 通达信（a-stock-data 参考实现）")
    parser.add_argument("code", help="6 位代码，如 600519")
    parser.add_argument("--check", choices=("bars", "finance"), default="bars")
    parser.add_argument("--bars", type=int, default=5, help="K线根数")
    parser.add_argument("--freq", type=int, default=4,
                        help="频率：0=5分 1=15分 2=30分 3=60分 4=日(默认) 5=周 6=月 8=1分 9=日(兼容别名)")
    args = parser.parse_args()

    client = tdx_client(check=args.check)
    if args.check == 'finance':
        print(f"=== {args.code} 财务快照（mootdx 通达信 TCP）===")
        df = client.finance(symbol=args.code)
        print(df.to_string() if df is not None and not df.empty else "(空)")
        return 0

    print(f"=== {args.code} 日线 {args.bars} 根（mootdx 通达信 TCP）===")
    bars = client.bars(symbol=args.code, frequency=args.freq, offset=args.bars)
    if bars is not None and not bars.empty:
        print(bars[['open', 'close', 'high', 'low', 'vol']].to_string())
    else:
        print("(空)")

    print(f"\n=== {args.code} 实时报价 ===")
    try:
        q = client.quotes(symbol=[args.code])
        if q is not None and not q.empty:
            for c in ('price', 'open', 'high', 'low', 'last_close', 'vol', 'amount', 'servertime'):
                if c in q.columns:
                    print(f"  {c}: {q[c].iloc[0]}")
    except Exception as e:
        print(f"  报价异常: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
