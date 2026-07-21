#!/usr/bin/env python3
"""
ETF Garden live quote HTTP server. Listens on 127.0.0.1:8766.
- ThreadingHTTPServer for concurrent browser requests
- 30s shared in-memory cache to reduce stock-api pressure across browser tabs
- /health is instant
- /api/etf-garden/live returns the 91-ETF formal pool from stock-api@2.7.3
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import threading
import http.server
import socketserver
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ETF_BLOG_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "scripts"))
from futures_compass_data import snapshot_with_fallback  # noqa: E402
STOCK_API_PACKAGE = "stock-api@2.7.3"
PORT = 8766
CACHE_TTL = 30
MAX_CODES = 100
SERVER_START = time.time()
FUTURES_CACHE_TTL = 60

GARDEN_POOL = [
    {"name": "上证50ETF华夏", "code": "510050", "market": "XSHG", "type": "宽基"},
    {"name": "沪深300ETF易方达", "code": "510310", "market": "XSHG", "type": "宽基"},
    {"name": "中证500ETF南方", "code": "510500", "market": "XSHG", "type": "宽基"},
    {"name": "中证1000ETF南方", "code": "512100", "market": "XSHG", "type": "宽基"},
    {"name": "中证2000ETF华泰柏瑞", "code": "563300", "market": "XSHG", "type": "宽基"},
    {"name": "创业板ETF易方达", "code": "159915", "market": "XSHE", "type": "宽基"},
    {"name": "科创50ETF华夏", "code": "588000", "market": "XSHG", "type": "宽基"},
    {"name": "恒生ETF华夏", "code": "159920", "market": "XSHE", "type": "海外"},
    {"name": "恒生科技ETF华夏", "code": "513180", "market": "XSHG", "type": "海外"},
    {"name": "恒生医疗ETF博时", "code": "513060", "market": "XSHG", "type": "海外"},
    {"name": "标普500ETF博时", "code": "513500", "market": "XSHG", "type": "海外"},
    {"name": "纳指ETF广发", "code": "159941", "market": "XSHE", "type": "海外"},
    {"name": "道琼斯ETF鹏华", "code": "513400", "market": "XSHG", "type": "海外"},
    {"name": "标普生物科技ETF嘉实", "code": "159502", "market": "XSHE", "type": "行业"},
    {"name": "德国ETF华安", "code": "513030", "market": "XSHG", "type": "海外"},
    {"name": "法国ETF华安", "code": "513080", "market": "XSHG", "type": "海外"},
    {"name": "日经ETF华夏", "code": "513520", "market": "XSHG", "type": "海外"},
    {"name": "沙特ETF南方", "code": "159329", "market": "XSHE", "type": "海外"},
    {"name": "印度基金LOF", "code": "164824", "market": "XSHE", "type": "海外"},
    {"name": "东南亚科技ETF华泰柏瑞", "code": "513730", "market": "XSHG", "type": "海外"},
    {"name": "中韩半导体ETF华泰柏瑞", "code": "513310", "market": "XSHG", "type": "海外"},
    {"name": "教育ETF博时", "code": "513360", "market": "XSHG", "type": "行业"},
    {"name": "消费ETF汇添富", "code": "159928", "market": "XSHE", "type": "行业"},
    {"name": "酒ETF鹏华", "code": "512690", "market": "XSHG", "type": "行业"},
    {"name": "医药ETF广发", "code": "159938", "market": "XSHE", "type": "行业"},
    {"name": "农业ETF富国", "code": "159825", "market": "XSHE", "type": "行业"},
    {"name": "半导体ETF国联安", "code": "512480", "market": "XSHG", "type": "行业"},
    {"name": "红利ETF易方达", "code": "515180", "market": "XSHG", "type": "行业"},
    {"name": "养殖ETF国泰", "code": "159865", "market": "XSHE", "type": "行业"},
    {"name": "科技ETF华宝", "code": "515000", "market": "XSHG", "type": "行业"},
    {"name": "电子ETF华宝", "code": "515260", "market": "XSHG", "type": "行业"},
    {"name": "游戏ETF华夏", "code": "159869", "market": "XSHE", "type": "行业"},
    {"name": "创新药ETF银华", "code": "159992", "market": "XSHE", "type": "行业"},
    {"name": "航空航天ETF华夏", "code": "159227", "market": "XSHE", "type": "行业"},
    {"name": "房地产ETF南方", "code": "512200", "market": "XSHG", "type": "行业"},
    {"name": "金融地产ETF广发", "code": "159940", "market": "XSHE", "type": "行业"},
    {"name": "可转债ETF博时", "code": "511380", "market": "XSHG", "type": "行业"},
    {"name": "钢铁ETF国泰", "code": "515210", "market": "XSHG", "type": "行业"},
    {"name": "传媒ETF广发", "code": "512980", "market": "XSHG", "type": "行业"},
    {"name": "信息技术ETF广发", "code": "159939", "market": "XSHE", "type": "行业"},
    {"name": "物流ETF银华", "code": "516530", "market": "XSHG", "type": "行业"},
    {"name": "银行ETF华宝", "code": "512800", "market": "XSHG", "type": "行业"},
    {"name": "养老ETF华宝", "code": "516560", "market": "XSHG", "type": "行业"},
    {"name": "电池ETF广发", "code": "159755", "market": "XSHE", "type": "行业"},
    {"name": "化工ETF鹏华", "code": "159870", "market": "XSHE", "type": "行业"},
    {"name": "汽车ETF国泰", "code": "516110", "market": "XSHG", "type": "行业"},
    {"name": "基建ETF银华", "code": "516950", "market": "XSHG", "type": "行业"},
    {"name": "医疗ETF华宝", "code": "512170", "market": "XSHG", "type": "行业"},
    {"name": "军工ETF国泰", "code": "512660", "market": "XSHG", "type": "行业"},
    {"name": "数字经济ETF鹏扬", "code": "560800", "market": "XSHG", "type": "行业"},
    {"name": "计算机ETF天弘", "code": "159998", "market": "XSHE", "type": "行业"},
    {"name": "豆粕ETF华夏", "code": "159985", "market": "XSHE", "type": "商品"},
    {"name": "煤炭ETF国泰", "code": "515220", "market": "XSHG", "type": "行业"},
    {"name": "家电ETF国泰", "code": "159996", "market": "XSHE", "type": "行业"},
    {"name": "证券ETF国泰", "code": "512880", "market": "XSHG", "type": "行业"},
    {"name": "旅游ETF富国", "code": "159766", "market": "XSHE", "type": "行业"},
    {"name": "稀土ETF嘉实", "code": "516150", "market": "XSHG", "type": "行业"},
    {"name": "金融科技ETF华宝", "code": "159851", "market": "XSHE", "type": "行业"},
    {"name": "上证指数ETF富国", "code": "510210", "market": "XSHG", "type": "宽基"},
    {"name": "软件ETF嘉实", "code": "159852", "market": "XSHE", "type": "行业"},
    {"name": "通信ETF国泰", "code": "515880", "market": "XSHG", "type": "行业"},
    {"name": "有色金属ETF南方", "code": "512400", "market": "XSHG", "type": "行业"},
    {"name": "华宝油气LOF", "code": "162411", "market": "XSHE", "type": "商品"},
    {"name": "人工智能ETF华富", "code": "515980", "market": "XSHG", "type": "行业"},
    {"name": "工业母机ETF国泰", "code": "159667", "market": "XSHE", "type": "行业"},
    {"name": "环保ETF广发", "code": "512580", "market": "XSHG", "type": "行业"},
    {"name": "黄金ETF华安", "code": "518880", "market": "XSHG", "type": "商品"},
    {"name": "电力ETF广发", "code": "159611", "market": "XSHE", "type": "行业"},
    {"name": "机器人ETF华夏", "code": "562500", "market": "XSHG", "type": "行业"},
    {"name": "电网设备ETF华夏", "code": "159326", "market": "XSHE", "type": "行业"},
    {"name": "光伏ETF华泰柏瑞", "code": "515790", "market": "XSHG", "type": "行业"},
]

UNIVERSE_PATH = ROOT / "data" / "etf-universe.json"
_universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
GARDEN_POOL = [item for item in _universe["items"] if item["tier"] == "formal"]
if len(GARDEN_POOL) != _universe["counts"]["formal"]:
    raise RuntimeError("formal ETF universe count mismatch")

_cache = {"data": None, "ts": 0.0}
_cache_lock = threading.Lock()
_fetch_lock = threading.Lock()
US_GARDEN_PATH = ROOT / "public" / "data" / "us-etf-garden.json"
US_POOL_PATH = ROOT / "public" / "data" / "us-etf-pool.json"
US_NY = ZoneInfo("America/New_York")
US_CACHE_TTL = 15
_us_cache = {"data": None, "ts": 0.0}
_us_cache_lock = threading.Lock()
_us_fetch_lock = threading.Lock()
_futures_cache = {"data": None, "ts": 0.0}
_futures_cache_lock = threading.Lock()
_futures_fetch_lock = threading.Lock()


def market_prefix(market: str) -> str:
    return "SH" if market == "XSHG" else "SZ"


def now_cn_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC+08:00", time.gmtime(time.time() + 8 * 3600))


def fetch_quotes() -> dict:
    codes = [f"{market_prefix(x['market'])}{x['code']}" for x in GARDEN_POOL]
    cmd = ["npx", "-y", STOCK_API_PACKAGE, "get-stocks", *codes]
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "stock-api failed").strip()[:500])
    raw = json.loads(proc.stdout)
    rows = raw if isinstance(raw, list) else raw.get("data") or []
    quote_map = {str(r.get("code", ""))[-6:]: r for r in rows if isinstance(r, dict) and r.get("code")}
    items = []
    for item in GARDEN_POOL:
        quote = quote_map.get(item["code"]) or {}
        now = quote.get("now")
        yesterday = quote.get("yesterday")
        try:
            change_pct = float(quote.get("percent")) * 100
        except Exception:
            change_pct = None
        items.append({
            "name": quote.get("name") or item["name"],
            "code": item["code"],
            "stock_code": f"{market_prefix(item['market'])}{item['code']}",
            "market": "sh" if item["market"] == "XSHG" else "sz",
            "type": item["type"],
            "price": now,
            "prev_close": yesterday,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "high": quote.get("high"),
            "low": quote.get("low"),
            "source": quote.get("source") or "stock-api",
        })
    return {
        "ok": True,
        "source": "stock-api@2.7.3",
        "generated_at": now_cn_text(),
        "fetched_at": time.time(),
        "latency_ms": round((time.time() - started) * 1000),
        "count": len(items),
        "items": items,
    }


def filter_data(data: dict, codes: list[str] | None = None) -> dict:
    if not codes:
        return data
    wanted = set(codes)
    filtered = [item for item in data.get("items", []) if item.get("code") in wanted]
    payload = dict(data)
    payload["requested_codes"] = codes
    payload["requested_count"] = len(codes)
    payload["count"] = len(filtered)
    payload["items"] = filtered
    return payload


def get_data(force: bool = False, codes: list[str] | None = None) -> dict:
    now = time.time()
    with _cache_lock:
        cached = _cache["data"]
        age = now - _cache["ts"]
        if cached and not force and age < CACHE_TTL:
            data = dict(cached)
            data["cache_age_s"] = round(age, 2)
            return filter_data(data, codes)
    with _fetch_lock:
        now = time.time()
        with _cache_lock:
            cached = _cache["data"]
            age = now - _cache["ts"]
            if cached and not force and age < CACHE_TTL:
                data = dict(cached)
                data["cache_age_s"] = round(age, 2)
                return filter_data(data, codes)
        data = fetch_quotes()
        with _cache_lock:
            _cache["data"] = data
            _cache["ts"] = time.time()
        data = dict(data)
        data["cache_age_s"] = 0
        return filter_data(data, codes)


def parse_codes(query: dict[str, list[str]]) -> tuple[list[str] | None, list[str]]:
    raw_values = query.get("codes", []) + query.get("code", [])
    raw = ",".join(raw_values)
    if not raw.strip():
        return None, []
    values = []
    seen = set()
    for chunk in raw.split(","):
        code = chunk.strip().upper()
        if code.startswith(("SH", "SZ")):
            code = code[2:]
        if code and code not in seen:
            seen.add(code)
            values.append(code)
    if len(values) > MAX_CODES:
        raise ValueError(f"at most {MAX_CODES} codes are allowed")
    formal = {item["code"] for item in GARDEN_POOL}
    invalid = [code for code in values if code not in formal]
    return values, invalid


def load_us_watchlist() -> list[dict]:
    garden = json.loads(US_GARDEN_PATH.read_text(encoding="utf-8"))
    pool = json.loads(US_POOL_PATH.read_text(encoding="utf-8"))
    pool_map = {row["symbol"]: row for row in pool.get("rows", []) if row.get("symbol")}
    symbols = {
        item.get("symbol")
        for items in (garden.get("flower_signals") or {}).values()
        for item in (items or [])
        if item.get("symbol")
    }
    symbols.update(item.get("symbol") for item in garden.get("recommendations", []) if item.get("symbol"))
    return [{"symbol": symbol, "name": pool_map.get(symbol, {}).get("name") or symbol} for symbol in sorted(symbols)]


def fetch_us_quote(item: dict) -> dict | None:
    symbol = item["symbol"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m&includePrePost=false"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ETF-Compass-Live/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)["chart"]["result"][0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote", [{}])[0]
    closes = quote.get("close") or []
    latest_index = next((i for i in range(min(len(timestamps), len(closes)) - 1, -1, -1) if closes[i] is not None), None)
    if latest_index is None:
        return None
    price = float(meta.get("regularMarketPrice") or closes[latest_index])
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    previous = float(previous) if previous not in (None, 0) else None
    change_pct = (price / previous - 1) * 100 if previous else None
    highs = [float(v) for v in (quote.get("high") or []) if v is not None]
    lows = [float(v) for v in (quote.get("low") or []) if v is not None]
    quote_time = datetime.fromtimestamp(timestamps[latest_index], timezone.utc).astimezone(US_NY).isoformat()
    return {
        "symbol": symbol,
        "name": item["name"],
        "price": round(price, 4),
        "prev_close": round(previous, 4) if previous is not None else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "high": round(max(highs), 4) if highs else None,
        "low": round(min(lows), 4) if lows else None,
        "quote_time": quote_time,
        "source": "yahoo-chart",
    }


def fetch_us_quotes(symbol: str | None = None) -> dict:
    watchlist = load_us_watchlist()
    if symbol:
        watchlist = [item for item in watchlist if item["symbol"] == symbol]
    started = time.time()
    items = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_us_quote, item): item["symbol"] for item in watchlist}
        for future in as_completed(futures):
            try:
                quote = future.result()
                if quote:
                    items.append(quote)
            except Exception:
                continue
    items.sort(key=lambda item: item["symbol"])
    now_ny = datetime.now(US_NY)
    return {
        "ok": len(items) >= max(1, int(len(watchlist) * 0.8)),
        "source": "yahoo-chart",
        "generated_at": now_ny.isoformat(),
        "fetched_at": time.time(),
        "latency_ms": round((time.time() - started) * 1000),
        "requested_count": len(watchlist),
        "count": len(items),
        "items": items,
    }


def get_us_data(force: bool = False, symbol: str | None = None) -> dict:
    if symbol:
        return fetch_us_quotes(symbol=symbol)
    now = time.time()
    with _us_cache_lock:
        cached = _us_cache["data"]
        age = now - _us_cache["ts"]
        if cached and not force and age < US_CACHE_TTL:
            data = dict(cached); data["cache_age_s"] = round(age, 2); return data
    with _us_fetch_lock:
        now = time.time()
        with _us_cache_lock:
            cached = _us_cache["data"]
            age = now - _us_cache["ts"]
            if cached and not force and age < US_CACHE_TTL:
                data = dict(cached); data["cache_age_s"] = round(age, 2); return data
        data = fetch_us_quotes()
        with _us_cache_lock:
            _us_cache["data"] = data
            _us_cache["ts"] = time.time()
        data = dict(data); data["cache_age_s"] = 0; return data


def get_futures_data(force: bool = False) -> dict:
    now = time.time()
    with _futures_cache_lock:
        cached = _futures_cache["data"]
        age = now - _futures_cache["ts"]
        if cached and not force and age < FUTURES_CACHE_TTL:
            data = dict(cached); data["cache_age_s"] = round(age, 2); return data
    with _futures_fetch_lock:
        now = time.time()
        with _futures_cache_lock:
            cached = _futures_cache["data"]
            age = now - _futures_cache["ts"]
            if cached and not force and age < FUTURES_CACHE_TTL:
                data = dict(cached); data["cache_age_s"] = round(age, 2); return data
        data = snapshot_with_fallback()
        with _futures_cache_lock:
            _futures_cache["data"] = data
            _futures_cache["ts"] = time.time()
        data = dict(data); data["cache_age_s"] = 0; return data


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")
        sys.stderr.flush()

    def _send_json(self, status: int, payload: dict, cacheable: bool = False):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=15, s-maxage=30, stale-while-revalidate=30" if cacheable else "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        url = urlparse(self.path)
        if url.path == "/health":
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        elif url.path in {"/api/etf-garden/live", "/api/us-etf-garden/live", "/api/futures-compass/live"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=15, s-maxage=30, stale-while-revalidate=30")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/health":
                with _cache_lock:
                    cache_age = time.time() - _cache["ts"] if _cache["data"] else None
                self._send_json(200, {"ok": True, "service": "etf-garden-live", "version": "1.5", "uptime_s": round(time.time() - SERVER_START), "cache_ttl_s": CACHE_TTL, "futures_cache_ttl_s": FUTURES_CACHE_TTL, "cache_age_s": round(cache_age, 2) if cache_age is not None else None})
            elif url.path == "/api/etf-garden/live":
                query = parse_qs(url.query)
                force = query.get("refresh", [""])[0] == "1"
                codes, invalid = parse_codes(query)
                if invalid:
                    self._send_json(400, {"ok": False, "error": "codes outside formal ETF pool", "invalid_codes": invalid})
                    return
                payload = get_data(force=force, codes=codes)
                self._send_json(200, payload, cacheable=not force)
            elif url.path == "/api/us-etf-garden/live":
                query = parse_qs(url.query)
                force = query.get("refresh", [""])[0] == "1"
                symbol = query.get("symbol", [""])[0].strip().upper()
                if symbol and symbol not in {item["symbol"] for item in load_us_watchlist()}:
                    self._send_json(404, {"ok": False, "error": "symbol not in current US action watchlist"})
                    return
                payload = get_us_data(force=force, symbol=symbol or None)
                self._send_json(200 if payload.get("ok") else 502, payload)
            elif url.path == "/api/futures-compass/live":
                query = parse_qs(url.query)
                force = query.get("refresh", [""])[0] == "1"
                payload = get_futures_data(force=force)
                self._send_json(200, payload, cacheable=not force)
            else:
                self._send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self._send_json(502, {"ok": False, "error": str(exc), "generated_at": now_cn_text()})


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = ThreadedHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"etf-garden-live listening on 127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
