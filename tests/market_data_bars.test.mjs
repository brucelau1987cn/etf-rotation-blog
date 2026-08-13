import assert from 'node:assert/strict';
import test from 'node:test';
import { onRequestGet, aggregateDailyBars, normalizeAShareSymbol } from '../functions/api/public/v1/market-data/bars.js';
import { baoStockSecCode } from '../functions/api/public/v1/_baostock.js';

const request = (url) => ({ request: new Request(`https://etf.peekabo.cc${url}`) });

test('market-data bars validates source, symbol, period, adjustment and limit', async () => {
  const response = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=600021&period=day&source=bad&limit=0'));
  assert.equal(response.status, 400);
  const body = await response.json();
  assert.equal(body.status, 'error');
  assert.match(body.message, /source|limit/i);
});

test('market-data normalizes common A-share symbols without losing exchange identity', () => {
  assert.deepEqual(normalizeAShareSymbol('600021', ''), { code: '600021', exchange: 'SSE', yahoo: '600021.SS' });
  assert.deepEqual(normalizeAShareSymbol('sh.000001', ''), { code: '000001', exchange: 'SSE', yahoo: '000001.SS' });
  assert.deepEqual(normalizeAShareSymbol('000001.SZ', ''), { code: '000001', exchange: 'SZSE', yahoo: '000001.SZ' });
  assert.equal(normalizeAShareSymbol('AAPL', ''), null);
  assert.equal(baoStockSecCode('sh.000001'), 'sh.000001');
  assert.equal(baoStockSecCode('sz.000001'), 'sz.000001');
});

test('market-data forwards explicit SSE identity to the BaoStock adapter', async () => {
  let received;
  const response = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=sh.000001&period=day&source=baostock&limit=1'), {
    fetchBaoStockImpl: async (symbol, exchange, period, adjustment) => {
      received = { symbol, exchange, period, adjustment };
      return [{ timestamp: 1, close: 1 }];
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(received, { symbol: 'sh.000001', exchange: '', period: 'day', adjustment: 'none' });
  const body = await response.json();
  assert.equal(body.exchange, 'SSE');
});

test('market-data rejects unsupported source, period and adjustment combinations', async () => {
  const baostockMinute = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=600021&period=1m&source=baostock'));
  assert.equal(baostockMinute.status, 400);
  const yahooAdjusted = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=AAPL&period=day&source=yahoo&adjustment=qfq'));
  assert.equal(yahooAdjusted.status, 400);
});

test('market-data bars returns a normalized Yahoo daily series', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    assert.match(String(url), /query1\.finance\.yahoo\.com\/v8\/finance\/chart\/AAPL/);
    return Response.json({ chart: { result: [{ meta: { exchangeTimezoneName: 'America/New_York' }, timestamp: [1704067200, 1704153600], indicators: { quote: [{ open: [1, 2], high: [2, 3], low: [0.5, 1.5], close: [1.5, 2.5], volume: [10, 20] }] } }] } });
  };
  try {
    const response = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=AAPL&exchange=NASDAQ&period=day&source=yahoo&adjustment=none&limit=2'));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.status, 'ok');
    assert.equal(body.source, 'yahoo');
    assert.equal(body.period, 'day');
    assert.equal(body.timezone, 'America/New_York');
    assert.deepEqual(body.items.map((item) => item.close), [1.5, 2.5]);
    assert.equal(body.items[0].timestamp, 1704067200000);
  } finally {
    globalThis.fetch = previous;
  }
});

test('market-data bars aggregates BaoStock daily rows into calendar weeks', () => {
  const rows = [
    { date: '2026-08-07', open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1000, hsl: 1 },
    { date: '2026-08-10', open: 11, high: 13, low: 10, close: 12, volume: 200, amount: 2200, hsl: 2 },
    { date: '2026-08-11', open: 12, high: 14, low: 11, close: 13, volume: 300, amount: 3600, hsl: 3 },
  ];
  const weekly = aggregateDailyBars(rows, 'week');
  assert.equal(weekly.length, 2);
  assert.deepEqual(weekly[1], { date: '2026-08-11', open: 11, high: 14, low: 10, close: 13, volume: 500, amount: 5800, hsl: 5 });
});

test('market-data auto A-share falls back to Yahoo when BaoStock is unavailable', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    assert.match(String(url), /query1\.finance\.yahoo\.com/);
    return Response.json({ chart: { result: [{ timestamp: [1704067200], indicators: { quote: [{ open: [1], high: [2], low: [0.5], close: [1.5], volume: [10] }] } }] } });
  };
  try {
    const response = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=600021&period=day&source=auto&limit=1'), {
      fetchBaoStockImpl: async () => { throw new Error('baostock down'); },
      fetchYahooImpl: async () => ({ items: [{ timestamp: 1704067200000, close: 1.5 }], timezone: 'Asia/Shanghai' }),
    });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.source, 'yahoo');
    assert.equal(body.items[0].close, 1.5);
  } finally {
    globalThis.fetch = previous;
  }
});

test('market-data bars reports unavailable TradingView explicitly', async () => {
  const response = await onRequestGet(request('/api/public/v1/market-data/bars?symbol=XAUUSD&exchange=OANDA&period=1h&source=tradingview&limit=3'));
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.status, 'error');
  assert.equal(body.code, 'UNAVAILABLE');
});
