import { fetchKlineFromBaoStock } from '../_baostock.js';

const PERIODS = new Set(['1m', '5m', '15m', '30m', '1h', 'day', 'week', 'month']);
const ADJUSTMENTS = new Set(['none', 'qfq', 'hfq']);
const MAX_LIMIT = 500;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': status >= 400 ? 'no-store' : 'public, max-age=10, s-maxage=10',
      'access-control-allow-origin': '*',
      'x-content-type-options': 'nosniff',
    },
  });
}

function number(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeYahoo(payload, symbol, period, adjustment, limit) {
  const result = payload?.chart?.result?.[0];
  if (!result) return { items: [], timezone: 'UTC' };
  const quote = result.indicators?.quote?.[0] || {};
  const rows = (result.timestamp || []).map((timestamp, i) => ({
    timestamp: timestamp * 1000,
    open: number(quote.open?.[i]),
    high: number(quote.high?.[i]),
    low: number(quote.low?.[i]),
    close: number(quote.close?.[i]),
    volume: number(quote.volume?.[i]),
  })).filter((row) => row.close != null);
  return { items: rows.slice(-limit), timezone: result.meta?.exchangeTimezoneName || result.meta?.timezone || 'UTC' };
}

async function fetchYahoo(symbol, exchange, period, adjustment, limit) {
  const interval = { '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1h', day: '1d', week: '1wk', month: '1mo' }[period];
  const range = ['1m', '5m', '15m', '30m', '1h'].includes(period) ? '30d' : '10y';
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=${range}&interval=${interval}&events=div%2Csplits`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 ETF-Compass/1.0' }, signal: controller.signal });
    if (!response.ok) throw new Error(`Yahoo HTTP ${response.status}`);
    const result = normalizeYahoo(await response.json(), symbol, period, adjustment, limit);
    if (!result.items.length) throw new Error('Yahoo empty series');
    return result;
  } finally {
    clearTimeout(timer);
  }
}

export function normalizeAShareSymbol(symbol, exchange) {
  const value = symbol.trim();
  let code;
  let market = exchange === 'SSE' || exchange === 'SH' ? 'SSE' : exchange === 'SZSE' || exchange === 'SZ' ? 'SZSE' : '';
  let match = value.match(/^(sh|sz)\.(\d{6})$/i);
  if (match) {
    market = match[1].toLowerCase() === 'sh' ? 'SSE' : 'SZSE';
    code = match[2];
  } else {
    match = value.match(/^(\d{6})\.(SH|SS|SZ)$/i);
    if (match) {
      market = /^(SH|SS)$/i.test(match[2]) ? 'SSE' : 'SZSE';
      code = match[1];
    } else if (/^\d{6}$/.test(value)) {
      code = value;
      if (!market) market = code.startsWith('6') ? 'SSE' : 'SZSE';
    }
  }
  if (!code || !market) return null;
  return { code, exchange: market, yahoo: `${code}.${market === 'SSE' ? 'SS' : 'SZ'}` };
}

function toBaoSymbol(symbol, exchange) {
  const parsed = normalizeAShareSymbol(symbol, exchange);
  if (!parsed) throw new Error('BaoStock requires an SH/SZ A-share symbol');
  return `${parsed.exchange === 'SSE' ? 'sh' : 'sz'}.${parsed.code}`;
}

function startOfWeek(date) {
  const value = new Date(`${date}T00:00:00Z`);
  const weekday = value.getUTCDay() || 7;
  value.setUTCDate(value.getUTCDate() - weekday + 1);
  return value.toISOString().slice(0, 10);
}

export function aggregateDailyBars(rows, period) {
  if (period === 'day') return rows;
  const buckets = new Map();
  for (const row of rows) {
    const key = period === 'week' ? startOfWeek(row.date) : row.date.slice(0, 7);
    const current = buckets.get(key);
    if (!current) {
      buckets.set(key, { ...row });
      continue;
    }
    current.date = row.date;
    current.high = Math.max(current.high, row.high);
    current.low = Math.min(current.low, row.low);
    current.close = row.close;
    current.volume += row.volume;
    current.amount = (current.amount || 0) + (row.amount || 0);
    current.hsl = (current.hsl || 0) + (row.hsl || 0);
  }
  return [...buckets.values()];
}

async function fetchBaoStock(symbol, exchange, period, adjustment, limit) {
  if (!['day', 'week', 'month'].includes(period)) throw new Error('BaoStock supports day/week/month bars only');
  const rows = aggregateDailyBars(await fetchKlineFromBaoStock(toBaoSymbol(symbol, exchange), adjustment === 'none' ? '' : adjustment), period);
  return rows.slice(-limit).map((row) => ({
    timestamp: Date.parse(`${row.date}T00:00:00+08:00`),
    date: row.date,
    open: number(row.open), high: number(row.high), low: number(row.low), close: number(row.close),
    volume: number(row.volume), amount: number(row.amount), turnoverRate: number(row.hsl),
  }));
}

export async function onRequestGet({ request }, { fetchBaoStockImpl = fetchBaoStock, fetchYahooImpl = fetchYahoo } = {}) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get('symbol') || '').trim();
  const exchange = (url.searchParams.get('exchange') || '').trim().toUpperCase();
  const period = (url.searchParams.get('period') || 'day').trim();
  const source = (url.searchParams.get('source') || 'auto').trim().toLowerCase();
  const adjustment = (url.searchParams.get('adjustment') || 'none').trim().toLowerCase();
  const rawLimit = url.searchParams.get('limit') || '100';
  const limit = Number(rawLimit);
  if (!symbol || symbol.length > 40 || !/^[A-Za-z0-9.=^_-]+$/.test(symbol) || exchange.length > 16 || !/^[A-Z0-9_-]*$/.test(exchange) || !['auto', 'yahoo', 'baostock', 'tradingview'].includes(source) || !PERIODS.has(period) || !ADJUSTMENTS.has(adjustment) || !/^[1-9]\d*$/.test(rawLimit) || !Number.isInteger(limit) || limit > MAX_LIMIT) {
    return json({ status: 'error', code: 'INVALID_REQUEST', message: 'symbol, exchange, source, period, adjustment or limit is invalid' }, 400);
  }
  const aShare = normalizeAShareSymbol(symbol, exchange);
  if (source === 'baostock' && (!aShare || !['day', 'week', 'month'].includes(period))) {
    return json({ status: 'error', code: 'INVALID_REQUEST', message: 'BaoStock supports SH/SZ day/week/month bars only' }, 400);
  }
  if (source === 'yahoo' && adjustment !== 'none') {
    return json({ status: 'error', code: 'INVALID_REQUEST', message: 'Yahoo supports adjustment=none only' }, 400);
  }
  if (source === 'tradingview') return json({ status: 'error', code: 'UNAVAILABLE', message: 'TradingView WebSocket source is unavailable in Pages Functions' }, 503);
  try {
    let actualSource = source;
    let items;
    let timezone;
    if (source === 'baostock') {
      items = await fetchBaoStockImpl(symbol, exchange, period, adjustment, limit);
      timezone = 'Asia/Shanghai';
    } else if (source === 'yahoo') {
      const result = await fetchYahooImpl(symbol, exchange, period, adjustment, limit);
      items = result.items;
      timezone = result.timezone;
    } else if (aShare && ['day', 'week', 'month'].includes(period)) {
      actualSource = 'baostock';
      try {
        items = await fetchBaoStockImpl(aShare.code, aShare.exchange, period, adjustment, limit);
        timezone = 'Asia/Shanghai';
      } catch {
        if (adjustment !== 'none') throw new Error('adjusted A-share fallback unavailable');
        actualSource = 'yahoo';
        const result = await fetchYahooImpl(aShare.yahoo, aShare.exchange, period, adjustment, limit);
        items = result.items;
        timezone = result.timezone;
      }
    } else {
      if (adjustment !== 'none') return json({ status: 'error', code: 'INVALID_REQUEST', message: 'adjustment is unsupported for this source' }, 400);
      actualSource = 'yahoo';
      const result = await fetchYahooImpl(symbol, exchange, period, adjustment, limit);
      items = result.items;
      timezone = result.timezone;
    }
    return json({ status: 'ok', source: actualSource, symbol, exchange: aShare?.exchange || exchange, period, adjustment, timezone, items });
  } catch (error) {
    console.error(JSON.stringify({ event: 'market_data_bars_error', source, symbol, period, message: String(error?.message || 'unknown') }));
    return json({ status: 'error', code: 'UNAVAILABLE', source, message: 'market data unavailable' }, 503);
  }
}

export default { async fetch(request) { return onRequestGet({ request }); } };
