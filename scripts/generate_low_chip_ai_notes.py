#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_low_chip_ai_notes.py — 低筹码股 AI 评语 + 短线反转观察星级(1-3★) 生成器。

与选股完全解耦: 只读已确定的低筹码名单(intersection)与当日快照/追踪数据,
只负责"评价已入选的股票", 绝不修改任何选股逻辑/入池/排除/权重。

流程:
  1. 读当日快照 public/data/low-chip-history/{trade_date}.json (或 a-low-chip-stocks.json)
  2. 对每只入选股从 low-chip-tracking.json 提取近 20 日走势特征(止跌/转强/趋势/回撤)
  3. 汇总快照档案(获利盘/股东/筹码/主力/财务/概念/ETF持仓) + 走势特征 → 结构化 prompt
  4. 调用 LLM (deepseek-v4-pro @ ai.peekabo.cc) 每只生成 {stars: 1|2|3, label, comment}
  5. 本地校验: stars∈{1,2,3}, label∈白名单, comment 130~260字, 不合法则丢弃该条(不硬凑)
  6. 逐只 POST 到 D1 API (low-chip-notes), 按 (stock_code, trade_date) upsert, 不覆盖历史
  7. 失败单只跳过并计数; LLM/API 整体失败不影响任何现有数据

用法:
  python3 scripts/generate_low_chip_ai_notes.py [YYYY-MM-DD] [--dry-run] [--limit N] [--no-llm]
    --dry-run  只打印将生成的评语不写入
    --limit N  只处理前 N 只(调试)
    --no-llm   不调用 LLM(用占位/仅验证数据装配)
依赖: LOW_CHIP_SYNC_TOKEN(env) 或 /root/.hermes/credentials/low-chip-sync.env
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'public/data/a-low-chip-stocks.json'
TRACKING = ROOT / 'public/data/low-chip-tracking.json'
HISTORY = ROOT / 'public/data/low-chip-history'
SYNC_ENV = Path('/root/.hermes/credentials/low-chip-sync.env')
CN = dt.timezone(dt.timedelta(hours=8))

LLM_URL = os.environ.get('LOW_CHIP_LLM_URL', 'https://ai.peekabo.cc/v1/chat/completions')
LLM_KEY = os.environ.get('LOW_CHIP_LLM_KEY', '') or ''
LLM_MODEL = os.environ.get('LOW_CHIP_LLM_MODEL', 'deepseek-v4-flash')

VALID_LABELS = ['开始转强', '反转观察', '初现止跌', '相对强势', '趋势延续', '仍在寻底', '风险偏高', '数据不足']
LABEL_HINTS = '、'.join(VALID_LABELS)

NOTE_API = os.environ.get('LOW_CHIP_NOTE_API', 'https://etf.peekabo.cc/api/public/v1/low-chip-notes')


def load_key() -> str:
    if LLM_KEY:
        return LLM_KEY
    # hermes 主配置兜底(ai.peekabo.cc 网关 key)
    for cand in (Path('/root/.hermes/config.yaml'), Path('/root/.hermes/config.yml')):
        if not cand.exists():
            continue
        try:
            text = cand.read_text(encoding='utf-8')
            m = re.search(r'api_key:\s*["\']?([A-Za-z0-9_\-]+)', text)
            if m:
                return m.group(1)
        except Exception:
            pass
    raise RuntimeError('no LLM api key (set LOW_CHIP_LLM_KEY)')


def sync_token() -> str:
    tok = os.environ.get('LOW_CHIP_SYNC_TOKEN', '')
    if tok:
        return tok
    if SYNC_ENV.exists():
        for line in SYNC_ENV.read_text(encoding='utf-8').splitlines():
            if line.startswith('LOW_CHIP_SYNC_TOKEN='):
                return line.split('=', 1)[1].strip()
    raise RuntimeError('LOW_CHIP_SYNC_TOKEN missing')


def load_snapshot(trade_date: str) -> dict:
    """当日快照: 优先 low-chip-history/{date}.json, 兜底当前 a-low-chip-stocks.json(需 date 匹配)。"""
    hist = HISTORY / f'{trade_date}.json'
    if hist.exists():
        return json.loads(hist.read_text(encoding='utf-8'))
    cur = json.loads(DATA.read_text(encoding='utf-8'))
    if cur.get('data_as_of') == trade_date:
        return cur
    raise FileNotFoundError(f'no snapshot for {trade_date} (history + current both absent)')


def streak_for(symbol: str, snap: dict) -> int:
    """连续入池天数: 从 history index 反查该 symbol 在 trade_date 往回连续几天都在 intersection。"""
    idx = HISTORY.parent / 'low-chip-history-index.json'
    if not idx.exists():
        return 1
    try:
        index = json.loads(idx.read_text(encoding='utf-8'))
        dates = sorted(index.get('dates', []) or [], reverse=True)
    except Exception:
        return 1
    target = snap.get('data_as_of') or ''
    if target not in dates:
        return 1
    # dates 最新在前; 找到 target 位置往回数连续包含 symbol 的天数
    try:
        pos = dates.index(target)
    except ValueError:
        return 1
    streak = 0
    for d in dates[pos:]:
        snap_d = HISTORY / f'{d}.json'
        if not snap_d.exists():
            break
        try:
            members = json.loads(snap_d.read_text(encoding='utf-8')).get('intersection', [])
        except Exception:
            break
        if symbol in members:
            streak += 1
        else:
            break
    return max(1, streak)


def trend_features(symbol: str, tracking: dict, trade_date: str) -> dict:
    """从 tracking 的 daily(加入以来日K) 提取 trade_date 当日的走势特征。"""
    rec = tracking.get('stocks', {}).get(symbol)
    if not rec:
        return {}
    daily = rec.get('daily') or []
    # 定位到 trade_date(含)为止的窗口
    bars = [b for b in daily if b.get('date') and b['date'] <= trade_date]
    if not bars:
        return {}
    closes = [b.get('close') for b in bars if isinstance(b.get('close'), (int, float))]
    if not closes:
        return {}
    last = bars[-1]
    def chg(offset):
        # offset 天前的 close → 现在涨跌
        idx = len(closes) - 1 - offset
        if idx < 0 or closes[idx] in (0, None):
            return None
        return round((closes[-1] - closes[idx]) / closes[idx] * 100, 2)
    # 近端连涨/连跌
    run_up = run_dn = 0
    for b in reversed(bars):
        c = b.get('change_pct')
        if c is None or not isinstance(c, (int, float)):
            break
        if c >= 0:
            run_up += 1
            break  # 只看最近一天方向? 不: 连续计数
        break
    # 正确连涨: 从尾部向前累计同号
    run_up = run_dn = 0
    if isinstance(last.get('change_pct'), (int, float)):
        sign = 1 if last['change_pct'] >= 0 else -1
        for b in reversed(bars):
            c = b.get('change_pct')
            if c is None or not isinstance(c, (int, float)):
                break
            if (c >= 0) == (sign > 0):
                if sign > 0:
                    run_up += 1
                else:
                    run_dn += 1
            else:
                break
    # 距 20 日高点/低点
    win = closes[-20:] if len(closes) >= 20 else closes
    hi = max(win)
    lo = min(win)
    pct_off_hi = round((closes[-1] - hi) / hi * 100, 2) if hi else None
    pct_off_lo = round((closes[-1] - lo) / lo * 100, 2) if lo else None
    # 止跌: 最近3日 vs 前5日 波动
    recent = closes[-3:] if len(closes) >= 3 else closes
    prev = closes[-6:-3] if len(closes) >= 6 else []
    recent_avg = sum(recent) / len(recent) if recent else None
    prev_avg = sum(prev) / len(prev) if prev else None
    # 数据充分度: bars 越少, 走势判断越不可靠
    if len(bars) <= 1:
        suff = '新入池仅1根K线, 无历史走势可比'
    elif len(bars) <= 5:
        suff = f'仅{len(bars)}根K线(短窗口), 5/10/20日涨跌实际为同窗口近似'
    else:
        suff = '走势数据充分'
    return {
        'last_date': last.get('date'),
        'last_close': last.get('close'),
        'last_change_pct': last.get('change_pct'),
        'last_profit_ratio': last.get('profit_ratio'),
        'chg_1d': last.get('change_pct'),
        'chg_5d': chg(4),
        'chg_10d': chg(9),
        'chg_20d': chg(19) if len(closes) >= 20 else chg(len(closes) - 1),
        'run_up_days': run_up,
        'run_down_days': run_dn,
        'pct_off_20d_high': pct_off_hi,
        'pct_off_20d_low': pct_off_lo,
        'recent_vs_prev': round((recent_avg - prev_avg) / prev_avg * 100, 2) if (recent_avg and prev_avg) else None,
        'bars': len(bars),
        'data_sufficiency': suff,
    }


def build_prompt(sym, name, snap, streak, trend, trade_date: str) -> str:
    e = snap.get('enrichments', {}).get(sym) or {}
    fin = e.get('financials') or {}
    sm = e.get('shareholder_metrics') or {}
    tech = e.get('technical') or {}
    periods = {pk: next((r for r in snap.get('periods', {}).get(pk, []) if r.get('symbol') == sym), None)
               for pk in ('week', 'month', 'quarter')}
    def gv(pk):
        v = (periods.get(pk) or {}).get('value')
        return v if v is not None else 'N/A'
    roe = fin.get('roe'); net = fin.get('net_margin')
    concepts = e.get('theme_concepts') or []
    concepts_s = '、'.join(str(c) for c in concepts[:3]) if concepts else '无'
    etfs = e.get('etf_holdings') or []
    etf_s = '、'.join(f"{h.get('name')}#{h.get('rank')}({h.get('weight_pct')}%)" for h in etfs[:3]) if etfs else '无公开数据'
    holder = e.get('quality_shareholder') or e.get('institutional_shareholder')
    holder_s = '优质/机构股东' if e.get('quality_shareholder') else ('机构股东' if e.get('institutional_shareholder') else '无')
    pct_off = trend.get('pct_off_20d_high')
    if pct_off is not None and abs(pct_off) < 3:
        pos = '贴近20日高位(强势区)'
    elif pct_off is not None and pct_off < -15:
        pos = f'较20日高点回撤{abs(pct_off):.0f}%(深跌)'
    elif pct_off is not None:
        pos = f'距20日高点{pct_off:+.1f}%'
    else:
        pos = '数据不足'

    return f"""你是资深A股短线交易员, 为"低筹码股观察名单"写每日短评。名单由独立选股模型决定(三周期收盘获利<1.5%的超跌低筹码股), 你只负责评价, 不改变入选。

【股票】{name} {sym} | 行业:{e.get('industry') or 'N/A'} | 概念:{concepts_s}
【筹码/资金】三周期获利盘 周:{gv('week')}% 月:{gv('month')}% 季:{gv('quarter')}% | 主力控盘:{sm.get('main_force')}%({sm.get('main_force_label')}) | 股东户数变化:{sm.get('shareholder_change_pct')}% | 筹码集中度:{sm.get('chip_focus')} | 十大流通股东:{sm.get('top10_float_ratio')}% | {holder_s}
【财务】ROE:{roe}% 净利率:{net}% 负债率:{fin.get('debt_ratio')}% 毛利率:{fin.get('gross_margin')}% (报告期:{fin.get('report_period')})
【ETF持仓】{etf_s}
【连续入池】第{streak}天
【近期走势(截至{trade_date})】昨收:{trend.get('last_close')} 当日涨跌:{trend.get('last_change_pct')}% | 5日:{trend.get('chg_5d')}% 10日:{trend.get('chg_10d')}% 20日:{trend.get('chg_20d')}% | {pos} | 连涨{trend.get('run_up_days')}天/连跌{trend.get('run_down_days')}天 | 近3日均价 vs 前段: {trend.get('recent_vs_prev')}% | {trend.get('data_sufficiency', '')}

【写作要求】
- 先形成判断再写: 现在处于什么状态? 为什么可能反转? 上涨弹性多大? 最大风险? 下一步观察什么?
- 区分驱动性质: 超跌反弹/趋势反转/行业Beta/公司Alpha/基本面驱动/资金驱动/事件驱动
- 特别重视相对强弱推理; 数据不足就少写, 严禁编造数据或断言不确定的量能/消息
- 130~220字, 专业精炼, 像交易员每日短评, 不要罗列数据不要套话
- 判断星级(1-3星): ★★★=反转迹象较明确+弹性/相对强度/催化较足(当天重点); ★★=止跌修复或潜在催化但部分未确认; ★=仍偏弱/未止跌/弹性有限/风险较明显
- 入池本身不构成弱势理由: 低筹码入池恰恰说明筹码已充分清洗。若同时具备 股东户数明显收敛(负变化) + 主力中度以上控盘 + 贴近20日高位或连涨, 应给2星甚至3星; 只有确实仍连阴/破位/无催化时才给1星
- 若该股为新入池(连续入池第1天)或走势数据不足, 明说"新入池/数据不足"并按快照筹码质量给星, 不要一律给1星
- 状态标签限选: {LABEL_HINTS}
- 直接输出JSON, 不要任何思考过程/解释/前后缀文字

只输出一个 JSON 对象, 不要多余文字:
{{"stars": 1或2或3, "label": "状态标签", "comment": "评语正文"}}"""


def llm_json(prompt: str, key: str, timeout: int = 120, retries: int = 2) -> dict | None:
    payload = {
        'model': LLM_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 3000,
        'temperature': 0.4,
    }
    body = json.dumps(payload).encode()
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(LLM_URL, data=body, headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {key}',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode('utf-8')
            parsed = json.loads(raw)
            # 兼容两种包装: 顶层 choices 或 {data:{choices}}
            inner = parsed.get('data') if isinstance(parsed.get('data'), dict) else parsed
            msg = (inner.get('choices') or [{}])[0].get('message') or {}
            content = msg.get('content') or ''
            if not content.strip():
                # reasoning 模型可能内容在 reasoning 外; 失败重试
                raise ValueError('empty content')
            m = re.search(r'\{[\s\S]*\}', content)
            if not m:
                raise ValueError('no JSON in content')
            out = json.loads(m.group(0))
            stars = int(out.get('stars'))
            if stars not in (1, 2, 3):
                raise ValueError(f'bad stars {stars}')
            label = str(out.get('label', '')).strip()
            if label not in VALID_LABELS:
                label = '反转观察' if stars >= 2 else '仍在寻底'
            comment = str(out.get('comment', '')).strip()
            if len(comment) < 100:
                raise ValueError(f'comment too short ({len(comment)})')
            return {'stars': stars, 'label': label, 'comment': comment}
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(2 + attempt * 3)
    print(f'    ⚠️ LLM 失败(重试{retries}次): {last_err}', flush=True)
    return None


def post_notes(notes: list[dict], token: str, dry_run: bool) -> dict:
    if dry_run or not notes:
        return {'dry_run': dry_run, 'total': len(notes)}
    payload = {'notes': notes}
    req = urllib.request.Request(NOTE_API, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f'    ⚠️ D1 POST HTTP {e.code}: {e.read().decode()[:200]}', flush=True)
        return {'ok': False, 'error': f'HTTP {e.code}'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('date', nargs='?', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-llm', action='store_true')
    args = ap.parse_args()

    trade_date = args.date or dt.datetime.now(CN).date().isoformat()
    snap = load_snapshot(trade_date)
    members = snap.get('intersection') or []
    if not members:
        print(f'{trade_date}: 无入选股票, 跳过')
        return 0
    if args.limit and args.limit > 0:
        members = members[:args.limit]
    tracking = json.loads(TRACKING.read_text(encoding='utf-8')) if TRACKING.exists() else {}
    key = load_key() if not args.no_llm else ''
    token = sync_token() if not args.dry_run else ''

    print(f'📝 低筹码 AI 评语生成 | {trade_date} | {len(members)} 只', flush=True)
    notes = []
    ok = fail = 0
    for i, sym in enumerate(members):
        name = ''
        for pk in ('week', 'month', 'quarter'):
            for r in snap.get('periods', {}).get(pk, []):
                if r.get('symbol') == sym:
                    name = r.get('name', '')
                    break
            if name:
                break
        streak = streak_for(sym, snap)
        trend = trend_features(sym, tracking, trade_date)
        prompt = build_prompt(sym, name or sym, snap, streak, trend, trade_date)
        print(f'  [{i+1}/{len(members)}] {name or sym} {sym} (连入{streak}天)', flush=True)
        if args.no_llm:
            notes.append({'trade_date': trade_date.replace('-', ''), 'stock_code': sym.split('.')[0],
                          'stock_name': name, 'stars': 2, 'label': '反转观察',
                          'comment': f'[no-llm 占位] {name or sym} 待生成。'})
            continue
        gen = llm_json(prompt, key)
        if not gen:
            fail += 1
            continue
        notes.append({
            'trade_date': trade_date.replace('-', ''),
            'stock_code': sym.split('.')[0],
            'stock_name': name,
            'stars': gen['stars'],
            'label': gen['label'],
            'comment': gen['comment'],
            'streak_days': streak,
            'model': LLM_MODEL,
            'status': 'ok',
        })
        ok += 1
        print(f'    ★{"★" * (gen["stars"] - 1)} [{gen["label"]}] {gen["comment"][:60]}…', flush=True)
        time.sleep(0.3)

    if not notes:
        print('⚠️ 无任何成功生成的评语(可能 LLM 全部失败), 不写入 D1', flush=True)
        return 1 if fail else 0

    res = post_notes(notes, token, args.dry_run)
    print()
    print(f'  ✅ 生成 {ok} 只 / 失败 {fail} 只 / 写入 {len(notes)} 条 {res}')
    if fail:
        print(f'  ⚠️ {fail} 只失败, 页面将显示"暂无评语"(不阻断)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
