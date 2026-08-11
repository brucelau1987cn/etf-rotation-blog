#!/usr/bin/env python3
"""
金银库存与利率数据采集脚本
本机运行，抓取各数据源 → public/data/precious-inventory.json
数据源：
  1. SHFE 上期所仓单日报（黄金/白银仓单库存）
  2. LBMA 伦敦库存（黄金/白银 vault data）
  3. FRED 实际利率（10yr TIPS）→ 改用 Treasury 实际收益率曲线
  4. CME COMEX 库存（待接入）
  5. Kitco 租赁利率（待接入）
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

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

# ─── 4. CME COMEX 库存（占位） ─────────────────────────
def fetch_cme():
    return {'ok': False, 'source': 'cme', 'error': '待接入替代源'}

# ─── 5. Kitco 租赁利率（占位） ─────────────────────────
def fetch_kitco():
    return {'ok': False, 'source': 'kitco', 'error': '待接入替代源'}

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
    kitco = fetch_kitco()

    output = {
        'status': 'ok',
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'data': {
            'shfe': shfe,
            'lbma': lbma,
            'fred': fred,
            'cme': cme,
            'kitco': kitco,
        },
    }

    # 写入 public/data/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.abspath(os.path.join(script_dir, '..'))
    out_path = os.path.join(project_dir, 'public', 'data', 'precious-inventory.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'  Written to {out_path}', flush=True)

    # 输出摘要
    ok_count = sum(1 for v in [shfe, lbma, fred] if v.get('ok'))
    print(f'  Result: {ok_count}/3 OK (SHFE+LBMA+FRED)', flush=True)

if __name__ == '__main__':
    main()