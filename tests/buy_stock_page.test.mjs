import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { onRequest as middleware } from '../functions/_middleware.js';
import { signToken } from '../functions/_lib/subscription-auth.js';
import { normalizePosition } from '../functions/_lib/buy-stock.js';

const SECRET = 'buy-stock-test-secret';
const page = await readFile(new URL('../src/pages/buy-stock.astro', import.meta.url), 'utf8');
const app = await readFile(new URL('../public/js/buy-stock-app.js', import.meta.url), 'utf8');
const config = await readFile(new URL('../astro.config.mjs', import.meta.url), 'utf8');
const migration = await readFile(new URL('../migrations/0022_buy_stock_positions.sql', import.meta.url), 'utf8');

const endpoint = await readFile(new URL('../functions/api/public/v1/buy-stock.js', import.meta.url), 'utf8');

test('the page exists at /buy-stock/ and is excluded from search engines', () => {
  assert.match(page, /<BaseHead/);
  assert.match(page, /name="robots" content="noindex, nofollow"/);
  assert.match(config, /sitemap\(\{ filter: sitemapFilter \}\)/);
  assert.match(config, /page\.includes\('\/buy-stock'\)/);
});

test('the page renders the holdings table with the fee-aware columns', () => {
  for (const id of ['bs-tbody', 'bs-stat-count', 'bs-stat-cost', 'bs-stat-value', 'bs-stat-pnl']) {
    assert.ok(page.includes(id), `page must contain ${id}`);
  }
  for (const label of ['买入价', '股数', '买入金额', '费用', '现价', '市值', '持仓成本', '浮动盈亏', '收益率', '买入时间']) {
    assert.ok(page.includes(label), `page must show the ${label} column`);
  }
});

test('the page loads the holdings API and the shared quote API', () => {
  assert.match(app, /\/api\/public\/v1\/buy-stock/);
  assert.match(app, /\/api\/public\/v1\/quote/);
  // Live quotes are reused from the public endpoint, never fetched server-side
  // inside the holdings endpoint, so the fee maths lives in one place.
  assert.doesNotMatch(endpoint, /fetchQuote/);
});

test('the page states the fee rules it applies', () => {
  assert.match(page, /0\.025%/);
  assert.match(page, /0\.05%/);
  assert.match(page, /0\.001%/);
});

test('the holdings table is gated behind a login', async () => {
  let nextCalled = false;
  const response = await middleware({
    request: new Request('https://etf.peekabo.cc/buy-stock/'),
    env: {},
    next: async () => { nextCalled = true; return new Response('page'); },
  });
  assert.equal(response.status, 302);
  assert.match(response.headers.get('Location') || '', /\/login\/\?next=/);
  assert.equal(nextCalled, false);
});

test('an admin can open the holdings page', async () => {
  const token = await signToken({ role: 'admin', sub: 'admin:2', exp: new Date(Date.now() + 3600_000).toISOString() }, SECRET);
  let nextCalled = false;
  const response = await middleware({
    request: new Request('https://etf.peekabo.cc/buy-stock/', { headers: { Cookie: `etf_admin=${token}` } }),
    env: { ADMIN_SECRET: SECRET },
    next: async () => { nextCalled = true; return new Response('page'); },
  });
  assert.equal(response.status, 200);
  assert.equal(nextCalled, true);
});

test('a subscriber with a live device session can open the holdings page', async () => {
  const token = await signToken({
    role: 'subscriber',
    sid: 'session-abc',
    exp: new Date(Date.now() + 3600_000).toISOString(),
  }, SECRET);
  const db = {
    prepare(sql) {
      return {
        sql,
        bind(...args) { this.args = args; return this; },
        async first() { return this.sql.includes('sub_sessions') ? { '1': 1 } : null; },
        async all() { return { results: [] }; },
        async run() { return { meta: { changes: 0 } }; },
      };
    },
  };
  let nextCalled = false;
  const response = await middleware({
    request: new Request('https://etf.peekabo.cc/buy-stock/', { headers: { Cookie: `etf_sub=${token}` } }),
    env: { SUBSCRIBE_SECRET: SECRET, DB: db },
    next: async () => { nextCalled = true; return new Response('page'); },
  });
  assert.equal(response.status, 200);
  assert.equal(nextCalled, true);
});

test('position normalisation accepts the shorthand the owner types', () => {
  const parsed = normalizePosition({
    code: '600860',
    buy_price: 7.45,
    shares: 5000,
    buy_time: '10/14 14:03',
  });
  assert.equal(parsed.error, undefined);
  assert.equal(parsed.position.buy_time, `${new Date().getUTCFullYear()}-10-14 14:03`);
  assert.equal(parsed.position.market, 'SH');
});

test('position normalisation accepts a full timestamp and infers the market', () => {
  const parsed = normalizePosition({
    code: '301696',
    buy_price: 75.65,
    shares: 1000,
    buy_time: '2026-10-15 09:42',
  });
  assert.equal(parsed.position.buy_time, '2026-10-15 09:42');
  assert.equal(parsed.position.buy_date, '2026-10-15');
  assert.equal(parsed.position.market, 'SZ');
});

test('position normalisation rejects invalid input', () => {
  assert.match(normalizePosition({ code: 'abc', buy_price: 1, shares: 1, buy_time: '2026-10-14 14:03' }).error, /6位数字/);
  assert.match(normalizePosition({ code: '600860', buy_price: 0, shares: 1, buy_time: '2026-10-14 14:03' }).error, /买入价/);
  assert.match(normalizePosition({ code: '600860', buy_price: 7.45, shares: 0, buy_time: '2026-10-14 14:03' }).error, /股数/);
  assert.match(normalizePosition({ code: '600860', buy_price: 7.45, shares: 100, buy_time: '' }).error, /买入时间/);
});

test('the D1 migration creates the positions table', () => {
  assert.match(migration, /CREATE TABLE IF NOT EXISTS buy_stock_positions/);
  assert.match(migration, /code TEXT PRIMARY KEY/);
  assert.match(migration, /buy_price REAL NOT NULL/);
  assert.match(migration, /shares INTEGER NOT NULL/);
  assert.match(migration, /buy_time TEXT/);
});
