import assert from 'node:assert/strict';
import test from 'node:test';

import { onRequestGet } from '../functions/api/internal/v1/baostock/[[path]].js';
import { handleBaoStockInternal } from '../functions/api/internal/v1/baostock/_handler.js';
import { parseBaoStockTableResponse } from '../functions/api/public/v1/_baostock.js';

const TOKEN = 'test-token';
const context = (path, token = TOKEN) => ({
  request: new Request(`https://etf.peekabo.cc${path}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {},
  }),
  env: { BAOSTOCK_API_TOKEN: TOKEN },
});

test('BaoStock internal route requires a configured valid bearer token', async () => {
  const missingConfig = await onRequestGet({ ...context('/api/internal/v1/baostock/calendar'), env: {} });
  assert.equal(missingConfig.status, 503);
  assert.equal((await missingConfig.json()).code, 'UNAVAILABLE');

  const unauthorized = await onRequestGet(context('/api/internal/v1/baostock/calendar', 'wrong'));
  assert.equal(unauthorized.status, 401);
  assert.equal(unauthorized.headers.get('cache-control'), 'no-store');
});

test('unknown BaoStock internal route returns not found without upstream access', async () => {
  const response = await handleBaoStockInternal(context('/api/internal/v1/baostock/unknown'));
  assert.equal(response.status, 404);
  assert.equal((await response.json()).code, 'NOT_FOUND');
});

test('calendar validates dates and returns normalized trading days with private cache headers', async () => {
  let received;
  const response = await handleBaoStockInternal(context('/api/internal/v1/baostock/calendar?start=2026-09-01&end=2026-09-03'), {
    fetchCalendarImpl: async (start, end) => {
      received = { start, end };
      return [
        { calendar_date: '2026-09-01', is_trading_day: '1' },
        { calendar_date: '2026-09-02', is_trading_day: '0' },
      ];
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(received, { start: '2026-09-01', end: '2026-09-03' });
  const body = await response.json();
  assert.deepEqual(body, {
    ok: true,
    source: 'baostock',
    start: '2026-09-01',
    end: '2026-09-03',
    count: 2,
    records: [
      { date: '2026-09-01', is_trading_day: true },
      { date: '2026-09-02', is_trading_day: false },
    ],
  });
  assert.match(response.headers.get('cache-control'), /^private, max-age=3600/);

    const spanCases = [
      '/api/internal/v1/baostock/calendar?start=2025-01-01&end=2026-01-02',
      '/api/internal/v1/baostock/calendar?end=x',
    ];
    for (const path of spanCases) {
      const badRange = await handleBaoStockInternal(context(path));
      assert.equal(badRange.status, 400, path);
    }

    const invalid = await handleBaoStockInternal(context('/api/internal/v1/baostock/calendar?start=2026-09-31&end=2026-09-01'));
  assert.equal(invalid.status, 400);
  assert.equal(invalid.headers.get('cache-control'), 'no-store');
});

test('qfq validates and caps symbol batch, date span and fields', async () => {
  const cases = [
    '/api/internal/v1/baostock/qfq?symbols=600000,000001,600001,600002,600003,600004&start=2026-09-01&end=2026-09-03',
    '/api/internal/v1/baostock/qfq?symbols=AAPL&start=2026-09-01&end=2026-09-03',
    '/api/internal/v1/baostock/qfq?symbols=600000&start=2024-01-01&end=2026-09-03',
    '/api/internal/v1/baostock/qfq?symbols=600000&start=2026-09-01&end=2026-09-03&fields=date,close,peTTM',
  ];
  for (const path of cases) {
    const response = await handleBaoStockInternal(context(path));
    assert.equal(response.status, 400, path);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  }
});

test('qfq fetches a small normalized OHLCV batch with explicit options', async () => {
  const calls = [];
  const response = await handleBaoStockInternal(context('/api/internal/v1/baostock/qfq?symbols=600000,000001.SZ&start=2026-09-01&end=2026-09-03&fields=date,code,open,high,low,close,volume,amount,turn'), {
    fetchKlineImpl: async (symbol, options) => {
      calls.push({ symbol, options });
      return [{ date: '2026-09-01', code: symbol, open: '10', high: '11', low: '9', close: '10.5', volume: '1000', amount: '10500', turn: '1.25' }];
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [
    { symbol: 'sh.600000', options: { adjust: 'qfq', start: '2026-09-01', end: '2026-09-03', fields: ['date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn'] } },
    { symbol: 'sz.000001', options: { adjust: 'qfq', start: '2026-09-01', end: '2026-09-03', fields: ['date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn'] } },
  ]);
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.adjust, 'qfq');
  assert.equal(body.count, 2);
  assert.equal(body.results[0].records[0].close, 10.5);
  assert.equal(body.results[0].records[0].volume, 1000);
  assert.match(response.headers.get('cache-control'), /^private, max-age=300/);
});

test('BaoStock table parser honors response fields for calendar and custom kline data', () => {
  const calendar = parseBaoStockTableResponse({ type: '34', fields: ['0', '', 'query_trade_dates', 'u', '1', '2000', '{"record":[["2026-09-01","1"]]}', '2026-09-01', '2026-09-01', 'calendar_date,is_trading_day'] }, '34');
  assert.deepEqual(calendar, [{ calendar_date: '2026-09-01', is_trading_day: '1' }]);

  const kline = parseBaoStockTableResponse({ type: '96', fields: ['0', '', 'query_history_k_data_plus', 'u', '1', '2000', '{"record":[["2026-09-01","10.5"]]}', 'sh.600000', 'date,close', '2026-09-01', '2026-09-01', 'd', '2'] }, '96');
  assert.deepEqual(kline, [{ date: '2026-09-01', close: '10.5' }]);
});
