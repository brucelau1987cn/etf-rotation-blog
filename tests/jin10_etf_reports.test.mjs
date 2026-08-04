import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../functions/api/public/v1/jin10-etf-reports.js', import.meta.url).pathname).href;
const mod = await import(moduleUrl);
const handler = mod.onRequestGet;

test('daily silver proxy requests attr_id + all=1 and keeps change=0 days', async () => {
  const previous = globalThis.fetch;
  let upstreamUrl = '';
  globalThis.fetch = async (url) => {
    upstreamUrl = String(url);
    return new Response(JSON.stringify({
      status: 200,
      data: [
        { trust: 15137.23, change: 0, value: 28196914203, reported_on: '2026-08-03', updated_at: '2026-08-03T23:00:28.000Z' },
        { trust: 15137.23, change: 89.97, value: 28081266684, reported_on: '2026-07-31', updated_at: '2026-07-31T22:15:53.000Z' },
        { trust: 15047.26, change: 0, value: 28074332148, reported_on: '2026-07-30', updated_at: '2026-07-30T22:15:39.000Z' },
      ],
      message: '',
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };

  try {
    const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-etf-reports?attr_id=2&limit=15');
    const response = await handler({ request, env: {} });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.asset, 'silver');
    assert.equal(payload.unit, 'day');
    assert.equal(payload.latest.reported_on, '2026-08-03');
    assert.equal(payload.latest.change, 0);
    assert.equal(payload.rows.length, 3);
    assert.equal(payload.rows[2].change, 0);
    assert.match(upstreamUrl, /attr_id=2/);
    assert.match(upstreamUrl, /all=1/);
    assert.doesNotMatch(upstreamUrl, /\/view/);
  } finally {
    globalThis.fetch = previous;
  }
});

test('daily gold proxy requests attr_id=1 + all=1', async () => {
  const previous = globalThis.fetch;
  let upstreamUrl = '';
  globalThis.fetch = async (url) => {
    upstreamUrl = String(url);
    return new Response(JSON.stringify({
      status: 200,
      data: [
        { trust: 1005.874, change: -1.141, value: 130220739713.21, reported_on: '2026-08-03', updated_at: '2026-08-03T22:15:13.000Z' },
      ],
      message: '',
    }), { status: 200 });
  };
  try {
    const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-etf-reports?attr_id=1&limit=5');
    const response = await handler({ request, env: {} });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.asset, 'gold');
    assert.equal(payload.latest.change, -1.141);
    assert.match(upstreamUrl, /attr_id=1/);
    assert.match(upstreamUrl, /all=1/);
  } finally {
    globalThis.fetch = previous;
  }
});

test('invalid attr_id returns 400', async () => {
  const response = await handler({
    request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-etf-reports?attr_id=9'),
    env: {},
  });
  assert.equal(response.status, 400);
});

test('fresh D1 rows short-circuit upstream fetch', async () => {
  const previous = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => {
    called = true;
    throw new Error('should not call upstream');
  };

  const db = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            async all() {
              if (String(sql).includes('SELECT')) {
                return {
                  results: Array.from({ length: 15 }, (_, i) => ({
                    reported_on: `2026-08-${String(3 - (i % 3)).padStart(2, '0')}`,
                    trust: 15137.23,
                    change: i === 0 ? 0 : 1,
                    value: 1,
                    raw_json: '{}',
                    synced_at: '2026-08-04T00:00:00.000Z',
                  })),
                };
              }
              return { results: [] };
            },
            async run() { return {}; },
          };
        },
        async run() { return {}; },
        async all() { return { results: [] }; },
      };
    },
  };

  try {
    // Make newest date look fresh relative to "now" by using today's ISO date
    const today = new Date().toISOString().slice(0, 10);
    db.prepare = (sql) => ({
      bind(...args) {
        return {
          async all() {
            if (String(sql).includes('SELECT')) {
              return {
                results: Array.from({ length: 15 }, (_, i) => ({
                  reported_on: today,
                  trust: 15137.23,
                  change: i === 0 ? 0 : 2,
                  value: 1,
                  raw_json: '{}',
                  synced_at: new Date().toISOString(),
                })),
              };
            }
            return { results: [] };
          },
          async run() { return {}; },
        };
      },
      async run() { return {}; },
      async all() { return { results: [] }; },
    });

    const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-etf-reports?attr_id=2&limit=15');
    const response = await handler({ request, env: { DB: db } });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.latest.change, 0);
    assert.equal(called, false);
  } finally {
    globalThis.fetch = previous;
  }
});
