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
  if (!result) return [];
  const quote = result.indicators?.quote?.[0] || {};
  const rows = (result.timestamp || []).map((timestamp, i) => ({
    timestamp: timestamp * 1000,
    open: number(quote.open?.[i]),
    high: number(quote.high?.[i]),
    low: number(quote.low?.[i]),
    close: number(quote.close?.[i]),
    volume: number(quote.volume?.[i]),
  })).filter((row) => row.close != null);
  return rows.slice(-limit);
}

async function fetchYahoo(symbol, exchange, period, adjustment, limit) {
  const interval = { '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1h', day: '1d', week: '1wk', month: '1mo' }[period];
  const range = ['1m', '5m', '15m', '30m', '1h'].includes(period) ? '30d' : '10y';
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=${range}&interval=${interval}&events=div%2Csplits`;
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 ETF-Compass/1.0' } });
  if (!response.ok) throw new Error(`Yahoo HTTP ${response.status}`);
  const items = normalizeYahoo(await response.json(), symbol, period, adjustment, limit);
  if (!items.length) throw new Error('Yahoo empty series');
  return items;
}

function toBaoSymbol(symbol, exchange) {
  const match = symbol.match(/^(sh|sz)\.(\d{6})$/i);
  if (match) return match[2];
  if (!/^\d{6}$/.test(symbol)) throw new Error('BaoStock requires a six-digit A-share symbol');
  return symbol;
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
  const rows = aggregateDailyBars(await fetchKlineFromBaoStock(toBaoSymbol(symbol, exchange), adjustment), period);
  return rows.slice(-limit).map((row) => ({
    timestamp: Date.parse(`${row.date}T00:00:00+08:00`),
    date: row.date,
    open: number(row.open), high: number(row.high), low: number(row.low), close: number(row.close),
    volume: number(row.volume), amount: number(row.amount), turnoverRate: number(row.hsl),
  }));
}

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get('symbol') || '').trim();
  const exchange = (url.searchParams.get('exchange') || '').trim().toUpperCase();
  const period = (url.searchParams.get('period') || 'day').trim();
  const source = (url.searchParams.get('source') || 'auto').trim().toLowerCase();
  const adjustment = (url.searchParams.get('adjustment') || 'none').trim().toLowerCase();
  const rawLimit = url.searchParams.get('limit') || '100';
  const limit = Number(rawLimit);
  if (!symbol || !['auto', 'yahoo', 'baostock', 'tradingview'].includes(source) || !PERIODS.has(period) || !ADJUSTMENTS.has(adjustment) || !/^[1-9]\d*$/.test(rawLimit) || !Number.isInteger(limit) || limit > MAX_LIMIT) {
    return json({ status: 'error', code: 'INVALID_REQUEST', message: 'symbol, source, period, adjustment or limit is invalid' }, 400);
  }
  if (source === 'tradingview') return json({ status: 'error', code: 'UNAVAILABLE', message: 'TradingView WebSocket source is unavailable in Pages Functions' }, 503);
  try {
    let actualSource = source;
    let items;
    if (source === 'baostock') {
      items = await fetchBaoStock(symbol, exchange, period, adjustment, limit);
    } else if (source === 'yahoo') {
      items = await fetchYahoo(symbol, exchange, period, adjustment, limit);
    } else if (/^\d{6}$/.test(symbol) && ['day', 'week', 'month'].includes(period)) {
      actualSource = 'baostock';
      items = await fetchBaoStock(symbol, exchange, period, adjustment, limit);
    } else {
      actualSource = 'yahoo';
      items = await fetchYahoo(symbol, exchange, period, adjustment, limit);
    }
    return json({ status: 'ok', source: actualSource, symbol, exchange, period, adjustment, timezone: 'Asia/Shanghai', items });
  } catch (error) {
    return json({ status: 'error', code: 'UNAVAILABLE', source, message: String(error?.message || 'upstream unavailable') }, 503);
  }
}

export default { async fetch(request) { return onRequestGet({ request }); } };
