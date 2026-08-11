/**
 * Cloudflare Pages Function - THS (10jqka) 数据代理
 * 代理同花顺无鉴权数据接口，统一 CORS + 缓存 + 参数校验 + 错误降级
 *
 * 路由（GET /api/public/v1/ths/*）：
 *   ping                      连通性探针（dq.10jqka 筹码日期目录）
 *   kline?code=169_GLD[&period=last|YYYY]      美股/A股 日K线（JSONP→JSON）
 *   today?code=169_GLD                         实时行情快照
 *   chip-list?code=600000&market=17[&days=90]  A股单标的筹码分布曲线（chip_type=all）
 *   chip-selection[?date=YYYY-MM-DD&page=1&size=20&sort=closing_profit&order=desc]  A股筹码选股列表
 *   search?q=GLD                               全球代码搜索（GBK→UTF-8）
 *
 * 上游：
 *   dq.10jqka.com.cn/fuyao/chip_shape_stock_selection/*  筹码
 *   d.10jqka.com.cn/v6/line/*                            行情 K线
 *   dict.hexin.cn:9531/stocks                            代码搜索
 */
const THS_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  'Accept': '*/*',
};

const DQ_BASE = 'https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection';
const D_BASE = 'https://d.10jqka.com.cn/v6/line';
const DICT_BASE = 'https://dict.hexin.cn:9531';

// 缓存 TTL（秒）：行情短、筹码/选股长
const TTL = { ping: 60, kline: 60, today: 30, 'chip-list': 300, 'chip-selection': 300, search: 60 };
const CACHE_MAX_AGE = 300;

function corsHeaders(extra = {}) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': `public, max-age=${CACHE_MAX_AGE}`,
    ...extra,
  };
}

function jsonOk(data, ttlSeconds) {
  return new Response(JSON.stringify({ ok: true, ...data }), {
    status: 200,
    headers: corsHeaders({ 'Cache-Control': `public, max-age=${ttlSeconds}` }),
  });
}

function jsonErr(msg, status = 502) {
  return new Response(JSON.stringify({ ok: false, msg }), { status, headers: corsHeaders() });
}

/** 上游请求：超时 + 状态检查，返回文本 */
async function proxyFetch(url, timeoutMs = 10000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { headers: THS_HEADERS, signal: ctrl.signal });
    const buf = await res.arrayBuffer();
    // 上游多为 UTF-8；dict 为 GBK
    let text;
    try {
      text = new TextDecoder('utf-8').decode(buf);
    } catch {
      text = '';
    }
    return { status: res.status, text, buf };
  } finally {
    clearTimeout(timer);
  }
}

/** JSONP -> JSON：quotebridge_v6_line_169_GLD_01_last({...}) */
function jsonpToJson(text) {
  const m = text.match(/^[^(]*\((.*)\)\s*;?\s*$/s);
  if (!m) return null;
  try {
    return JSON.parse(m[1]);
  } catch {
    return null;
  }
}

function jsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

const CODE_RE = /^[A-Za-z0-9._-]{1,24}$/;

/** 13 位毫秒时间戳（UTC+8 日期起点） */
function tsOf(dateStr) {
  return String(Date.parse(`${dateStr}T00:00:00+08:00`));
}

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const { pathname, searchParams } = url;
  const sub = pathname.replace(/^\/api\/public\/v1\/ths\/?/, '') || '';
  const p = (k, d) => (searchParams.get(k) ?? d);

  try {
    // ── ping：连通性探针 ──
    if (sub === 'ping') {
      const { status, text } = await proxyFetch(`${DQ_BASE}/selection/v1/date/list/all?chip_type=1`);
      const body = jsonParse(text);
      if (status !== 200 || !body || body.status_code !== 0) {
        return jsonErr(`upstream ${status}`, 502);
      }
      return jsonOk({
        source: 'dq.10jqka.com.cn',
        latest: body.data?.latest_select_at ?? null,
        dates: body.data?.date?.length ?? 0,
      }, TTL.ping);
    }

    // ── kline：日/年 K 线 ──
    if (sub === 'kline') {
      const code = p('code', '169_GLD');
      const period = p('period', 'last');
      if (!CODE_RE.test(code)) return jsonErr('bad code', 400);
      const { status, text } = await proxyFetch(`${D_BASE}/${encodeURIComponent(code)}/01/${encodeURIComponent(period)}.js`);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      const data = jsonpToJson(text);
      if (!data) return jsonErr('parse failed', 502);
      const recs = (data.data || '').split(';').filter((r) => r.trim());
      return jsonOk({ source: 'd.10jqka.com.cn', code, name: data.name, records: recs.length, data }, TTL.kline);
    }

    // ── today：实时行情快照 ──
    if (sub === 'today') {
      const code = p('code', '169_GLD');
      if (!CODE_RE.test(code)) return jsonErr('bad code', 400);
      const { status, text } = await proxyFetch(`${D_BASE}/${encodeURIComponent(code)}/01/today.js`);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      const data = jsonpToJson(text);
      if (!data || !data[code]) return jsonErr('parse failed', 502);
      return jsonOk({ source: 'd.10jqka.com.cn', code, quote: data[code] }, TTL.today);
    }

    // ── chip-list：A股单标的筹码分布曲线 ──
    if (sub === 'chip-list') {
      const code = p('code', '600000');
      const market = p('market', '17'); // 17=沪 33=深
      const days = parseInt(p('days', '90'), 10);
      if (!CODE_RE.test(code)) return jsonErr('bad code', 400);
      if (!['17', '33'].includes(market)) return jsonErr('market must be 17(沪)/33(深)', 400);
      const end = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10); // UTC+8 今天
      const start = new Date(Date.now() + 8 * 3600e3 - days * 86400e3).toISOString().slice(0, 10);
      const url = `${DQ_BASE}/stock/v1/chip_list?chip_type=all&stock_code=${encodeURIComponent(code)}&stock_market=${market}&start_date=${tsOf(start)}&end_date=${tsOf(end)}`;
      const { status, text } = await proxyFetch(url);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      const body = jsonParse(text);
      if (!body || body.status_code !== 0) return jsonErr('upstream error', 502);
      const list = body.data?.list ?? {};
      // 汇总最近一天 summary + 曲线点数
      const dates = Object.keys(list).sort();
      const last = dates.length ? list[dates[dates.length - 1]] : null;
      return jsonOk({
        source: 'dq.10jqka.com.cn', code, market,
        dates, count: dates.length,
        last_date: dates[dates.length - 1] ?? null,
        summary: last?.summary ?? null,
        curve_points: last?.curve_data?.list?.length ?? 0,
        data: list,
      }, TTL['chip-list']);
    }

    // ── chip-selection：A股筹码选股列表（获利盘排序） ──
    if (sub === 'chip-selection') {
      const date = p('date', '');
      const page = Math.max(1, parseInt(p('page', '1'), 10) || 1);
      const size = Math.min(100, Math.max(1, parseInt(p('size', '20'), 10) || 20));
      const sort = p('sort', 'closing_profit');
      const order = p('order', 'desc');
      const chipType = p('chip_type', '1');
      const shapeType = p('shape_type', '1');
      const dateParam = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : '';
      const q = [
        `offset_num=${(page - 1) * size}`,
        `page_size=${size}`,
        `shape_type=${encodeURIComponent(shapeType)}`,
        `chip_type=${encodeURIComponent(chipType)}`,
        `sort_field=${encodeURIComponent(sort)}`,
        `sort_order=${encodeURIComponent(order)}`,
        'filter_selfstock=0',
        ...(dateParam ? [`date=${dateParam}`] : []),
      ].join('&');
      const { status, text } = await proxyFetch(`${DQ_BASE}/selection/v1/list?${q}`);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      const body = jsonParse(text);
      if (!body || body.status_code !== 0) return jsonErr('upstream error', 502);
      const list = body.data?.list ?? [];
      return jsonOk({
        source: 'dq.10jqka.com.cn',
        date: body.data?.date ?? null,
        total: body.data?.total ?? list.length,
        count: list.length,
        list,
      }, TTL['chip-selection']);
    }

    // ── search：全球代码搜索 ──
    if (sub === 'search') {
      const q = p('q', '');
      if (!/^[A-Za-z0-9]{1,12}$/.test(q)) return jsonErr('bad q', 400);
      const marketType = p('markettype', '2');
      const url = `${DICT_BASE}/stocks?pattern=${encodeURIComponent(q)}&isauto=1&associate=0&pl=i&isrealcode=1&markettype=${encodeURIComponent(marketType)}`;
      const { status, buf } = await proxyFetch(url);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      // dict 返回 GBK
      const text = new TextDecoder('gbk').decode(buf);
      const body = jsonParse(text);
      if (!body) return jsonErr('parse failed', 502);
      const rows = (body.data || '').split('\n').filter(Boolean).map((line) => {
        const [code, name, py, market, flag, marketName, fullCode] = line.split('|');
        return { code, name, py, market, marketName, fullCode };
      });
      return jsonOk({ source: 'dict.hexin.cn', q, count: rows.length, list: rows }, TTL.search);
    }

    // ── stock-signal：主力意图信号（apigate.10jqka.com.cn，无鉴权；8/6 起数据源停更）──
    if (sub === 'stock-signal') {
      const code = p('code', '002026');
      if (!CODE_RE.test(code)) return jsonErr('bad code', 400);
      const url = `https://apigate.10jqka.com.cn/d/charge/eachtradedata/marketing/v1/stock_signal?stock_code=${encodeURIComponent(code)}&has_auth=false`;
      const { status, text } = await proxyFetch(url);
      if (status !== 200) return jsonErr(`upstream ${status}`, 502);
      const body = jsonParse(text);
      if (!body || body.status_code !== 0) return jsonErr('upstream error', 502);
      const rows = body.data ?? [];
      // 汇总统计（非 null 的意图分布 + 最近有效日）
      const valid = rows.filter((r) => r.main_intention !== null);
      const lastValid = valid.length ? valid[valid.length - 1] : null;
      return jsonOk({
        source: 'apigate.10jqka.com.cn',
        code,
        count: rows.length,
        last_valid_date: lastValid?.trade_date ?? null,
        last_main_intention: lastValid?.main_intention ?? null,
        data: rows,
      }, 300);
    }

    return jsonErr('unknown route: ping|kline|today|chip-list|chip-selection|search|stock-signal', 404);
  } catch (e) {
    return jsonErr(`internal: ${e && e.message || e}`, 500);
  }
}
