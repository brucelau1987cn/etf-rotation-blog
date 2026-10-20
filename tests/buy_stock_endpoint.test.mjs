import test from 'node:test';
import assert from 'node:assert/strict';

import { onRequestGet, onRequestPost } from '../functions/api/public/v1/buy-stock.js';
import { signToken } from '../functions/_lib/subscription-auth.js';

const SECRET = 'buy-stock-endpoint-test-secret';

function mockDb(rows = []) {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      const stmt = {
        sql,
        args: [],
        bind(...args) { stmt.args = args; return stmt; },
        async run() { calls.push({ sql, args: stmt.args, kind: 'run' }); return { meta: { changes: 1 } }; },
        async first() { calls.push({ sql, args: stmt.args, kind: 'first' }); return null; },
        async all() { calls.push({ sql, args: stmt.args, kind: 'all' }); return { results: rows }; },
      };
      return stmt;
    },
  };
}

const POSITION_ROWS = [
  { code: '600860', name: '京城股份', market: 'SH', buy_price: 7.45, shares: 5000, buy_time: '2026-10-14 14:03', buy_date: '2026-10-14', note: null, sold_at: null, sell_price: null, sell_time: null },
  { code: '301696', name: '三瑞智能', market: 'SZ', buy_price: 75.65, shares: 1000, buy_time: '2026-10-15 09:42', buy_date: '2026-10-15', note: null, sold_at: null, sell_price: null, sell_time: null },
];

function adminRequest(url, init = {}) {
  return new Request(url, {
    ...init,
    headers: { Cookie: `etf_admin=${init.__token}`, ...(init.headers || {}) },
  });
}

async function adminToken() {
  return signToken({ role: 'admin', sub: 'admin:2', exp: new Date(Date.now() + 3600_000).toISOString() }, SECRET);
}

test('the endpoint refuses anonymous readers', async () => {
  const response = await onRequestGet({
    request: new Request('https://etf.peekabo.cc/api/public/v1/buy-stock'),
    env: { DB: mockDb(POSITION_ROWS) },
  });
  assert.equal(response.status, 401);
  const payload = await response.json();
  assert.equal(payload.ok, false);
});

test('an admin reads the positions with their cost basis', async () => {
  const token = await adminToken();
  const response = await onRequestGet({
    request: adminRequest('https://etf.peekabo.cc/api/public/v1/buy-stock', { __token: token }),
    env: { DB: mockDb(POSITION_ROWS), ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.positions.length, 2);
  assert.equal(payload.open_count, 2);
  const first = payload.positions[0];
  assert.equal(first.code, '600860');
  assert.equal(first.buy_price, 7.45);
  assert.equal(first.shares, 5000);
  assert.equal(first.buy_time, '2026-10-14 14:03');
});

test('the endpoint never returns fee-adjusted P&L — the client owns that maths', async () => {
  const token = await adminToken();
  const response = await onRequestGet({
    request: adminRequest('https://etf.peekabo.cc/api/public/v1/buy-stock', { __token: token }),
    env: { DB: mockDb(POSITION_ROWS), ADMIN_SECRET: SECRET },
  });
  const payload = await response.json();
  const first = payload.positions[0];
  assert.equal('unrealized_pnl' in first, false);
  assert.equal('cost' in first, false);
});

test('an admin can create a position', async () => {
  const token = await adminToken();
  const db = mockDb(POSITION_ROWS);
  const response = await onRequestPost({
    request: adminRequest('https://etf.peekabo.cc/api/public/v1/buy-stock', {
      method: 'POST',
      __token: token,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: '002368', buy_price: 11.92, shares: 5000, buy_time: '2026-10-15 09:32' }),
    }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const insert = db.calls.find((call) => call.sql.includes('INSERT INTO buy_stock_positions'));
  assert.ok(insert, 'the endpoint must insert the position');
  assert.ok(insert.args.includes('002368'));
  assert.ok(insert.args.includes(11.92));
  assert.ok(insert.args.includes(5000));
  assert.ok(insert.args.includes('2026-10-15 09:32'));
});

test('a non-admin cannot read the positions', async () => {
  // The holdings are the owner's private positions; subscribers are excluded.
  const token = await signToken({ role: 'subscriber', exp: new Date(Date.now() + 3600_000).toISOString() }, SECRET);
  const response = await onRequestGet({
    request: new Request('https://etf.peekabo.cc/api/public/v1/buy-stock', { headers: { Cookie: `etf_sub=${token}` } }),
    env: { DB: mockDb(POSITION_ROWS), SUBSCRIBE_SECRET: SECRET, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 401);
});

test('an invalid position is rejected with a readable reason', async () => {
  const token = await adminToken();
  const response = await onRequestPost({
    request: adminRequest('https://etf.peekabo.cc/api/public/v1/buy-stock', {
      method: 'POST',
      __token: token,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: 'abc', buy_price: 1, shares: 1, buy_time: '2026-10-14 14:03' }),
    }),
    env: { DB: mockDb(POSITION_ROWS), ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 400);
  const payload = await response.json();
  assert.match(payload.error, /6位数字/);
});
