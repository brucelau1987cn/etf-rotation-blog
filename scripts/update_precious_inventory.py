#!/usr/bin/env python3
"""
金银库存与利率数据采集脚本
本机运行，抓取各数据源 → public/data/precious-inventory.json
数据源：
  1. SHFE 上期所仓单日报（黄金/白银仓单库存）
  2. LBMA 伦敦库存（黄金/白银 vault data）
  3. FRED 实际利率（10yr TIPS）→ 改用 Treasury 实际收益率曲线
  4. CME COMEX 库存（thevaultreport）
  5. 隐含租赁利率（COMEX 期货 + 美债收益率自算；Kitco 官方 lease 已下线）
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

try:
    from implied_lease_rate import fetch_implied_lease
except ImportError:
    # Allow `python3 scripts/update_precious_inventory.py` without package install.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from implied_lease_rate import fetch_implied_lease

# UTC+8 北京时间
CN_TZ = timezone(timedelta(hours=8))

def today_str():
    d = datetime.now(CN_TZ)
    return d.strftime('%Y%m%d')

def prev_trade_date_str():
    d = datetime.now(CN_TZ) - timedelta(days=1)
    while d.weekday() >= 5:  # 周末
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')

def fetch(url, ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'):
    req = Request(url, headers={
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode('utf-8', errors='replace')

# ─── 1. SHFE 上期所仓单 ────────────────────────────────
def fetch_shfe():
    dates = [today_str(), prev_trade_date_str()]
    for d in dates:
        try:
            url = f'https://www.shfe.com.cn/data/tradedata/future/stockdata/dailystock_{d}/ZH/all.html'
            html = fetch(url, ua='Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)')
            # 黄金：找 <div class="cell">黄金</div> 后的第一个 td 数字
            gold_idx = html.find('黄金</div>')
            gold_kg = 0
            if gold_idx >= 0:
                gold_table = html[gold_idx:html.index('</table>', gold_idx)]
                m = re.search(r'<td[^>]*>(\d[\d,.]*)</td>', gold_table)
                if m:
                    gold_kg = int(m.group(1).replace(',', ''))
            # 白银：找 <div class="cell">白银</div> 后的「总计」行
            silver_idx = html.find('白银</div>')
            silver_kg = 0
            if silver_idx >= 0:
                silver_table = html[silver_idx:html.index('</table>', silver_idx)]
                m = re.search(r'总计[\s\S]*?<td[^>]*>(\d[\d,.]*)</td>', silver_table)
                if m:
                    silver_kg = int(m.group(1).replace(',', ''))
            if gold_kg > 0 or silver_kg > 0:
                report_date = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                return {
                    'ok': True,
                    'source': 'shfe',
                    'date': d,
                    'reportDate': report_date,
                    'gold': {'kg': gold_kg, 'tonnes': round(gold_kg / 1000, 3)},
                    'silver': {'kg': silver_kg, 'tonnes': round(silver_kg / 1000, 3)},
                    'unit': '千克',
                    'note': '仓单库存（已注册标准仓单）',
                }
        except Exception as e:
            continue
    return {'ok': False, 'source': 'shfe', 'error': 'no data'}

# ─── 2. LBMA 伦敦库存 ──────────────────────────────────
def fetch_lbma():
    try:
        url = 'https://www.lbma.org.uk/vault-holdings-data/data.json'
        raw = fetch(url, ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        rows = json.loads(raw)
        latest = rows[-1] if rows else None
        prev = rows[-2] if len(rows) > 1 else None
        if latest:
            return {
                'ok': True,
                'source': 'lbma',
                'total': len(rows),
                'latest': {
                    'date': datetime.fromtimestamp(latest[0] / 1000, tz=timezone.utc).strftime('%Y-%m'),
                    'goldT': latest[1],
                    'silverT': latest[2],
                },
                'change': {
                    'goldT': round(latest[1] - prev[1]) if prev else 0,
                    'silverT': round(latest[2] - prev[2]) if prev else 0,
                } if prev else None,
            }
        return {'ok': False, 'source': 'lbma', 'error': 'empty data'}
    except Exception as e:
        return {'ok': False, 'source': 'lbma', 'error': str(e)}

# ─── 3. FRED 实际利率 → Treasury 实际收益率曲线 ────────
def fetch_real_yield():
    try:
        url = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
               'daily-treasury-rates.csv/2026/all?type=daily_treasury_real_yield_curve'
               '&field_tdr_date_value=2026&_format=csv')
        csv = fetch(url, ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        lines = csv.strip().split('\n')
        if len(lines) < 2:
            return {'ok': False, 'source': 'fred', 'error': 'empty CSV'}
        headers = lines[0].split(',')
        # 找 10 YR 列
        try:
            col_10yr = [i for i, h in enumerate(headers) if '10 YR' in h][0]
        except IndexError:
            col_10yr = 3  # 第4列
        values = []
        for line in lines[1:]:
            parts = line.split(',')
            if len(parts) > col_10yr and parts[col_10yr].strip():
                try:
                    val = float(parts[col_10yr])
                    if -10 < val < 20 and parts[0].strip():
                        values.append({'date': parts[0].strip(), 'value': val})
                except ValueError:
                    pass
        if not values:
            return {'ok': False, 'source': 'fred', 'error': 'no valid values'}
        # CSV 是降序（最新日期在第一行），recent 保持最新在前
        latest = values[0]
        return {
            'ok': True,
            'source': 'fred',
            'series': 'DFII10',
            'title': '10年期TIPS实际收益率（%）',
            'latest': latest,
            'recent': values[:30],
            'total': len(values),
        }
    except Exception as e:
        return {'ok': False, 'source': 'fred', 'error': str(e)}

# ─── 4. CME COMEX 库存 — 从 thevaultreport.com 解析 ──
def fetch_cme():
    try:
        url = 'https://thevaultreport.com/comex'
        html = fetch(url, ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        # 解析黄金/白银 registered + total
        # 黄金: 14.19M oz (金色 #D4A017, text-3xl) | Total 26.60M oz
        # 白银: 99.70M oz (text-3xl) | Total 333.97M oz
        gold_reg = 0
        gold_total = 0
        silver_reg = 0
        silver_total = 0

        # 找所有大数字
        big_nums = re.findall(r'text-3xl font-black tabular-nums[^>]*>([\d.]+M oz)', html)
        # 找所有 total 数字
        totals = re.findall(r'Total</p>.*?<p[^>]*>([\d.]+M oz)', html, re.DOTALL)

        if big_nums:
            gold_reg = parse_moz(big_nums[0])  # 14.19M oz (gold)
            silver_reg = parse_moz(big_nums[1]) if len(big_nums) > 1 else 0  # 99.70M oz (silver)
        if len(totals) >= 2:
            gold_total = parse_moz(totals[0])  # 26.60M
            silver_total = parse_moz(totals[1])  # 333.97M
        elif len(totals) == 1:
            gold_total = parse_moz(totals[0])

        gold_eligible = round(gold_total - gold_reg, 2) if gold_total > gold_reg else 0
        silver_eligible = round(silver_total - silver_reg, 2) if silver_total > silver_reg else 0

        # 更新日期
        date_match = re.search(r'as of (\w+ \d+, 202\d)', html)
        cme_date = date_match.group(1) if date_match else 'unknown'

        if gold_reg > 0 or silver_reg > 0:
            return {
                'ok': True,
                'source': 'cme',
                'date': cme_date,
                'gold': {
                    'registered': f'{gold_reg}M oz',
                    'eligible': f'{gold_eligible}M oz',
                    'total': f'{gold_total}M oz',
                    'registeredOz': gold_reg * 1_000_000,
                    'totalOz': gold_total * 1_000_000,
                },
                'silver': {
                    'registered': f'{silver_reg}M oz',
                    'eligible': f'{silver_eligible}M oz',
                    'total': f'{silver_total}M oz',
                    'registeredOz': silver_reg * 1_000_000,
                    'totalOz': silver_total * 1_000_000,
                },
                'note': 'Registered = 可交割（已注册仓单）；Eligible = 可注册（未注册仓单）',
            }
        return {'ok': False, 'source': 'cme', 'error': 'parse failed'}
    except Exception as e:
        return {'ok': False, 'source': 'cme', 'error': str(e)}

def parse_moz(s):
    s = s.strip().replace('M oz', '').replace('Moz', '')
    return float(s)

# ─── 5. 隐含租赁利率（COMEX 期货 + 美债） ──────────────
def fetch_lease():
    try:
        return fetch_implied_lease()
    except Exception as e:
        return {
            'ok': False,
            'source': 'implied_lease',
            'method': 'comex_forward_proxy',
            'error': str(e),
        }

# ─── 6. 黄金/白银 ETF 日/周/月线获利比（iWenCai） ──────
# 标的：黄金以 GLD（SPDR Gold Shares）为主，白银以 SLV（iShares Silver Trust）为主。
# iWenCai 美股代码 GLD.P / SLV.P，目前仅提供日线「美股@收盘获利」；周/月线美股无字段 → None。
ETF_PROFIT_SYMBOLS = {
    'gold': 'GLD',
    'silver': 'SLV',
}
ETF_PROFIT_LABELS = {
    'gold': 'GLD黄金ETF',
    'silver': 'SLV白银ETF',
}
IWENCAI_QUERY = '/root/.hermes/scripts/iwencai-market-query'


def _run_iwencai(query: str, limit: int = 2, timeout: int = 45) -> list[dict]:
    import subprocess
    import time
    last_err = ''
    for attempt in range(3):
        r = subprocess.run(
            [IWENCAI_QUERY, '-q', query, '--limit', str(limit), '--timeout', str(timeout)],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            last_err = f'exit {r.returncode}: {r.stderr[:120]}'
            time.sleep(2 * (attempt + 1))
            continue
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            last_err = f'json: {e}'
            time.sleep(2 * (attempt + 1))
            continue
        if not d.get('success'):
            last_err = f"api: {d.get('error') or d.get('msg') or 'unknown'}"
            time.sleep(2 * (attempt + 1))
            continue
        return d.get('datas') or []
    print(f'  [iwencai retry exhausted] {query}: {last_err}', flush=True)
    return []


def fetch_etf_profit_ratios() -> dict:
    """日/周/月线收盘获利比例（THS 同花顺 K线自算，三角分布+固定窗口无衰减）
    模型（以同花顺 App 三档标答校准，2026-08-12）：
      - 日线：最近 103 个交易日，无衰减累积，三角分布峰值=收盘价
      - 周线：最近 60 周（日K按 ISO 周聚合）
      - 月线：全历史（日K按自然月聚合）
    获利比 = 现价下方筹码占比。GLD 自算 vs App：日 44.6/40.85、周 74.6/70.40、月 92.7/91.32。
    """
    import urllib.request
    import urllib.error
    import datetime as _dt
    import time

    def _fetch_kline(code: str, period: str) -> list[dict]:
        # THS d.10jqka.com.cn kline returns HTTP 502/504 sporadically (2026-09-01 session
        # left both gold/silver ok=False with "no kline" because the year-loop got 8/11 502s
        # in a row). Bounded retry on transient HTTP faults and empty/string payloads before
        # returning empty (per skill ths-10jqka-data-apis + low-chip Tencent retry recipe).
        url = f'https://d.10jqka.com.cn/v6/line/{code}/01/{period}.js'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        last_err = ''
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    txt = r.read().decode('utf-8', 'replace')
                m = re.search(r'\((.*)\)\s*$', txt, re.S)
                if not m:
                    last_err = 'no_match'
                    time.sleep(1 + attempt * 2)
                    continue
                d = json.loads(m.group(1))
                raw = d.get('data') or ''
                if not isinstance(raw, str) or not raw:
                    last_err = 'empty_data'
                    time.sleep(1 + attempt * 2)
                    continue
                recs = []
                for rec in raw.split(';'):
                    f = rec.split('~')[0].split(',')
                    if len(f) >= 6 and f[0].isdigit():
                        try:
                            recs.append({'date': f[0], 'open': float(f[1]), 'high': float(f[2]), 'low': float(f[3]),
                                         'close': float(f[4]), 'vol': float(f[5])})
                        except (TypeError, ValueError):
                            continue
                if not recs:
                    last_err = 'no_parsable_rows'
                    time.sleep(1 + attempt * 2)
                    continue
                return recs
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = str(e)
                time.sleep(1 + attempt * 2)
                continue
        print(f'  [ths kline retry exhausted] {code}/{period}: {last_err}', flush=True)
        return []

    def _agg(recs, freq, drop_incomplete=False):
        """周=交易周（丢弃未完成周），月=自然月（含本月至今）"""
        out, cur = [], None
        for r in recs:
            dt = _dt.datetime.strptime(r['date'], '%Y%m%d')
            key = dt.isocalendar()[:2] if freq == 'week' else (dt.year, dt.month)
            if cur is None or cur['key'] != key:
                if cur:
                    out.append(cur['rec'])
                cur = {'key': key, 'rec': dict(r)}
            else:
                cur['rec']['high'] = max(cur['rec']['high'], r['high'])
                cur['rec']['low'] = min(cur['rec']['low'], r['low'])
                cur['rec']['close'] = r['close']
                cur['rec']['vol'] += r['vol']
        if cur:
            out.append(cur['rec'])
        if drop_incomplete and out:
            out = out[:-1]  # 丢弃未完成周期
        return out

    def _chip_profit(recs, price, window=None):
        """固定窗口无衰减 + 三角分布（峰值收盘价）→ 获利盘%"""
        if window:
            recs = recs[-window:]
        if not recs:
            return None
        lo = min(r['low'] for r in recs)
        hi = max(r['high'] for r in recs)
        span = hi - lo
        step = 0.01 if span <= 50 else (0.2 if span <= 100 else (0.5 if span <= 300 else 1.0))
        buckets = {}
        p = lo
        while p <= hi + 1e-9:
            buckets[p] = 0.0
            p = round(p + step, 6)
        keys = sorted(buckets.keys())
        for r in recs:
            vol = r['vol']
            lo_r, hi_r = r['low'], r['high']
            if hi_r <= lo_r:
                hi_r = lo_r + step
            mid = r['close']
            if not (lo_r <= mid <= hi_r):
                mid = (lo_r + hi_r) / 2
            ta = 0.0
            for pk in keys:
                if pk < lo_r or pk > hi_r:
                    continue
                w = (pk - lo_r) / (mid - lo_r) if pk <= mid and mid > lo_r else ((hi_r - pk) / (hi_r - mid) if pk > mid and hi_r > mid else 1.0)
                w = max(0.0, w)
                buckets[pk] += vol * w
                ta += vol * w
            if ta > 0:
                sc = vol / ta
                for pk in keys:
                    if lo_r <= pk <= hi_r:
                        buckets[pk] *= sc
        total = sum(buckets.values())
        if total <= 0:
            return None
        return sum(v for p, v in buckets.items() if p <= price) / total * 100

    out = {'ok': True, 'source': 'ths-kline', 'as_of': datetime.now(CN_TZ).strftime('%Y-%m-%d'), 'assets': {}}
    # 窗口按标的校准（App 标答 2026-08-12）：GLD 103天 / SLV 115天（SLV 5-6月高位筹码需更长窗口）
    WINDOWS = {'gold': 103, 'silver': 115}
    for key, symbol in ETF_PROFIT_SYMBOLS.items():
        try:
            recs_all = []
            for year in range(2016, 2027):
                period = str(year) if year < 2026 else 'last'
                try:
                    recs_all += _fetch_kline(f'169_{symbol}', period)
                except Exception:
                    pass
            seen = {}
            for r in recs_all:
                seen[r['date']] = r
            daily = [seen[k] for k in sorted(seen.keys())]
            if not daily:
                raise RuntimeError('no kline')
            # 基准价解耦：同花顺美股 K 线最新一根有结算延迟（如 8/11 close=401.89 vs 腾讯 400.96）
            # → 现价用腾讯官方收盘价，K 线只管筹码分布形状
            try:
                tq = urllib.request.urlopen(urllib.request.Request(
                    f'https://qt.gtimg.cn/q=us{symbol}',
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}), timeout=15).read().decode('gbk', 'ignore')
                # 腾讯格式: v_usGLD="200~名称~代码~现价~昨收~开~...  (split('~')[3] = 现价)
                m = re.search(r'"\d+~[^~]*~[^~]*~([\d.]+)~', tq)
                price = float(m.group(1)) if m else float(daily[-1]['close'])
            except Exception:
                price = float(daily[-1]['close'])
            week = _agg(daily, 'week', drop_incomplete=True)
            month = _agg(daily, 'month', drop_incomplete=False)
            day_p = _chip_profit(daily, price, WINDOWS[key])
            week_p = _chip_profit(week, price, 60)
            month_p = _chip_profit(month, price, None)
            out['assets'][key] = {
                'ok': day_p is not None,
                'symbol': f'169_{symbol}',
                'name': ETF_PROFIT_LABELS[key],
                'price': price,
                'change_percent': None,
                'day': round(day_p, 2) if day_p is not None else None,
                'week': round(week_p, 2) if week_p is not None else None,
                'month': round(month_p, 2) if month_p is not None else None,
            }
            print(f'  THS[{key}] day={out["assets"][key]["day"]} week={out["assets"][key]["week"]} month={out["assets"][key]["month"]} (price={price})', flush=True)
        except Exception as e:
            out['ok'] = False
            out['assets'][key] = {'ok': False, 'error': f'ths: {e}'}
    return out

# ─── 主流程 ────────────────────────────────────────────
def main():
    print('[precious-inventory] fetching...', flush=True)
    shfe = fetch_shfe()
    print(f'  SHFE: {"OK" if shfe.get("ok") else "FAIL"}', flush=True)
    lbma = fetch_lbma()
    print(f'  LBMA: {"OK" if lbma.get("ok") else "FAIL"}', flush=True)
    fred = fetch_real_yield()
    print(f'  FRED: {"OK" if fred.get("ok") else "FAIL"}', flush=True)
    cme = fetch_cme()
    print(f'  CME: {"OK" if cme.get("ok") else "FAIL"}', flush=True)
    lease = fetch_lease()
    print(f'  LEASE: {"OK" if lease.get("ok") else "FAIL"}', flush=True)
    etf_profit = fetch_etf_profit_ratios()
    for k, v in etf_profit.get('assets', {}).items():
        print(f'  ETF_PROFIT[{k}]: {"OK" if v.get("ok") else "FAIL"} day={v.get("day")} week={v.get("week")} month={v.get("month")}', flush=True)

    # THS 同花顺 kline 在 9/1 整天 502/504 (commit 79e66da 加了 retry 但仍全失败)：
    # gold/silver 任意一个 ok=False 时，从上次发布的 DATA 中合并成功的 ratios，
    # 保留展示连续性，避免 daily update cron 因临时网络问题整体失败。
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.abspath(os.path.join(script_dir, '..'))
    out_path = os.path.join(project_dir, 'public', 'data', 'precious-inventory.json')
    if os.path.exists(out_path):
        try:
            prev = json.load(open(out_path, encoding='utf-8'))
            prev_profit = (prev.get('data') or {}).get('etf_profit') or {}
            prev_assets = prev_profit.get('assets') or {}
            merged = etf_profit.setdefault('assets', {})
            for k in ('gold', 'silver'):
                row = merged.get(k) or {}
                if row.get('ok') and row.get('day') is not None:
                    continue  # 当前拉取成功则保留
                if k in prev_assets and (prev_assets[k] or {}).get('ok'):
                    merged[k] = prev_assets[k]
                    merged[k]['fallback'] = 'previous_publish'
                    print(f'  ETF_PROFIT[{k}]: restored from previous publish', flush=True)
            if all((merged.get(k) or {}).get('ok') for k in ('gold', 'silver')):
                etf_profit['ok'] = True
                etf_profit.setdefault('warnings', []).append('partial_restore_from_previous_publish')
        except (OSError, ValueError, KeyError) as exc:
            print(f'  WARN: failed to merge previous ETF profits: {exc}', flush=True)
    # Keep kitco key as alias so older clients still resolve the lease panel.
    kitco_alias = dict(lease)
    kitco_alias['legacy_key'] = 'kitco'
    kitco_alias['alias_of'] = 'implied_lease'

    output = {
        'status': 'ok',
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'data': {
            'shfe': shfe,
            'lbma': lbma,
            'fred': fred,
            'cme': cme,
            'implied_lease': lease,
            'kitco': kitco_alias,  # backward-compatible alias
            'etf_profit': etf_profit,  # 日/周/月线获利比
        },
    }

    # 写入 public/data/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.abspath(os.path.join(script_dir, '..'))
    out_path = os.path.join(project_dir, 'public', 'data', 'precious-inventory.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f'  Written to {out_path}', flush=True)

    # 输出摘要
    ok_count = sum(1 for v in [shfe, lbma, fred, cme, lease] if v.get('ok'))
    g1 = (lease.get('gold') or {}).get('rate_1m')
    s1 = (lease.get('silver') or {}).get('rate_1m')
    print(f'  Result: {ok_count}/5 OK (SHFE+LBMA+FRED+CME+LEASE)', flush=True)
    if lease.get('ok'):
        print(f'  Lease 1M gold={g1} silver={s1}', flush=True)

if __name__ == '__main__':
    main()