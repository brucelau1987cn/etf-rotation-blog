import test from 'node:test';
import assert from 'node:assert/strict';

import { onRequest as middleware } from '../functions/_middleware.js';
import { onRequest as lowChipMetrics } from '../functions/api/public/v1/low-chip-metrics.js';
import { signToken } from '../functions/_lib/subscription-auth.js';

const SECRET = 'privacy-test-secret';

test('anonymous visitors cannot download private low-chip strategy datasets', async () => {
  for (const path of [
    '/data/a-low-chip-stocks.json',
    '/data/low-chip-history-index.json',
    '/data/low-chip-history/2026-08-14.json',
    '/data/low-chip-tracking.json',
    '/data/model-lab/low-chip-fuyao-shadow.json',
    '/data/%61-low-chip-stocks.json',
    '/data/a-low-chip-stocks%2ejson',
    '/data/low-chip-history%2f2026-08-14.json',
    '//data/a-low-chip-stocks.json',
    '/data/%2561-low-chip-stocks.json',
    '/data/a-low-chip-stocks%252ejson',
    '/data/low-chip-history%252f2026-08-14.json',
    '/data%255ca-low-chip-stocks.json',
    '/data/%25252561-low-chip-stocks.json',
    '/data%2525255ca-low-chip-stocks.json',
  ]) {
    let nextCalled = false;
    const response = await middleware({
      request: new Request(`https://etf.peekabo.cc${path}`),
      env: {},
      next: async () => { nextCalled = true; return new Response('asset'); },
    });
    assert.equal(response.status, 404, path);
    assert.equal(nextCalled, false, path);
  }

  let encodedA = '%61';
  for (let depth = 0; depth < 11; depth += 1) encodedA = encodeURIComponent(encodedA);
  const deeplyEncodedPath = `/data/${encodedA}-low-chip-stocks.json`;
  let deepNextCalled = false;
  const deepResponse = await middleware({
    request: new Request(`https://etf.peekabo.cc${deeplyEncodedPath}`),
    env: {},
    next: async () => { deepNextCalled = true; return new Response('asset'); },
  });
  assert.equal(deepResponse.status, 400);
  assert.equal(deepNextCalled, false);
});

test('anonymous visitors cannot query low-chip historical metrics', async () => {
  const response = await lowChipMetrics({
    request: new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics?date=2026-08-14'),
    env: {},
  });
  assert.equal(response.status, 401);
});

test('authenticated users cannot download raw low-chip strategy datasets', async () => {
  const token = await signToken({ role: 'admin', sub: 'admin:2', exp: new Date(Date.now() + 3600_000).toISOString() }, SECRET);
  let nextCalled = false;
  const response = await middleware({
    request: new Request('https://etf.peekabo.cc/data/a-low-chip-stocks.json', {
      headers: { Cookie: `etf_admin=${token}` },
    }),
    env: { ADMIN_SECRET: SECRET },
    next: async () => { nextCalled = true; return new Response('asset'); },
  });
  assert.equal(response.status, 404);
  assert.equal(nextCalled, false);
});

test('authenticated admin can query low-chip historical metrics', async () => {
  const token = await signToken({ role: 'admin', sub: 'admin:2', exp: new Date(Date.now() + 3600_000).toISOString() }, SECRET);
  const calls = [];
  const db = {
    prepare(sql) {
      return {
        args: [],
        bind(...args) { this.args = args; calls.push({ sql, args }); return this; },
        async run() { return { meta: { changes: 0 } }; },
        async all() { return { results: [{ stock_code: '600000', stock_name: '测试', week_profit: 1.2, month_profit: 1.3, quarter_profit: 1.4 }] }; },
      };
    },
  };
  const response = await lowChipMetrics({
    request: new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics?date=2026-08-14', {
      headers: { Cookie: `etf_admin=${token}` },
    }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.count, 1);
  assert.equal(payload.results[0].stock_code, '600000');
  assert.equal(calls.some((call) => call.sql.includes('WHERE trade_date = ?') && call.args[0] === '20260814'), true);
  assert.equal(calls.some((call) => call.sql.includes('week_profit IS NOT NULL') && call.sql.includes('month_profit IS NOT NULL') && call.sql.includes('quarter_profit IS NOT NULL')), true);
  assert.equal('week_profit' in payload.results[0], false);
  assert.equal('month_profit' in payload.results[0], false);
  assert.equal('quarter_profit' in payload.results[0], false);
});

test('sync bearer can verify low-chip historical metrics after publication', async () => {
  const db = {
    prepare() {
      return {
        bind() { return this; },
        async run() { return { meta: { changes: 0 } }; },
        async all() { return { results: [{ trade_date: '20260814', stock_code: '600221', stock_name: '海航控股' }] }; },
      };
    },
  };
  const response = await lowChipMetrics({
    request: new Request('https://etf.peekabo.cc/api/public/v1/low-chip-metrics?date=2026-08-14', {
      headers: { Authorization: 'Bearer sync-secret' },
    }),
    env: { DB: db, LOW_CHIP_SYNC_TOKEN: 'sync-secret' },
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.trade_date, '20260814');
  assert.equal(payload.count, 1);
});
