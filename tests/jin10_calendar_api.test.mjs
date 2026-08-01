import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../functions/api/public/v1/jin10-calendar.js', import.meta.url).pathname).href;
const mod = await import(moduleUrl);
const handler = mod.onRequestGet;

test('jin10 calendar proxy validates dates and normalizes daily data', async () => {
  const previous = globalThis.fetch;
  let upstreamUrl = '';
  let upstreamHeaders;
  globalThis.fetch = async (url, options = {}) => {
    upstreamUrl = String(url);
    upstreamHeaders = new Headers(options.headers);
    return new Response(JSON.stringify({
      status: 200,
      message: 'OK',
      data: [
        { type: 'data', data: { data_id: 1182651, indicator_id: 534, pub_time: '2026-07-31 09:30', country: '中国', star: 3, indicator_name: '中国制造业PMI', previous: '50.3', consensus: '50.1', actual: '49.2', unit: '' } },
        { type: 'event', data: { id: 1148410, event_time: '2026-07-31 10:00', country: '日本', star: 3, event_content: '日本央行公布利率决议。' } },
      ],
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  };

  try {
    const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31');
    const response = await handler({ request });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.date, '2026-07-31');
    assert.equal(payload.count, 2);
    assert.deepEqual(payload.counts, { data: 1, event: 1, holiday: 0, other: 0 });
    assert.equal(payload.items[0].id, 1182651);
    assert.equal(payload.items[0].indicator_id, 534);
    assert.equal(payload.items[0].title, '中国制造业PMI');
    assert.equal(payload.items[0].star, 3);
    assert.equal(payload.items[0].show_affect, null);
    assert.equal(payload.items[1].title, '日本央行公布利率决议。');
    assert.match(upstreamUrl, /start_date=2026-07-31/);
    assert.match(upstreamUrl, /end_date=2026-07-31/);
    assert.equal(upstreamHeaders.get('x-app-id'), 'fiXF2nOnDycGutVA');
    assert.equal(upstreamHeaders.get('x-version'), '2.0');
  } finally {
    globalThis.fetch = previous;
  }
});

test('jin10 calendar proxy rejects invalid and oversized ranges', async () => {
  const badDate = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026/07/31') });
  assert.equal(badDate.status, 400);

  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ status: 200, message: 'OK', data: [] }), { status: 200 });
  try {
    const allowed31Days = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?start_date=2026-01-01&end_date=2026-01-31') });
    assert.equal(allowed31Days.status, 200);
  } finally {
    globalThis.fetch = previous;
  }

  const tooWide = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?start_date=2026-01-01&end_date=2026-02-01') });
  assert.equal(tooWide.status, 400);
});

test('jin10 calendar proxy clamps malformed stars and preserves unknown types', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: 200,
    message: 'OK',
    data: [
      { type: 'data', data: { data_id: 1, pub_time: '2026-07-31 01:00', indicator_name: '异常负星', star: -2 } },
      { type: 'event', data: { id: 2, event_time: '2026-07-31 02:00', event_content: '异常超星', star: 9 } },
      { type: 'notice', data: { id: 3, event_time: '2026-07-31 03:00', summary: '其他类型', star: 2 } },
    ],
  }), { status: 200 });
  try {
    const response = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31') });
    const payload = await response.json();
    assert.deepEqual(payload.items.map((item) => item.star), [0, 5, 2]);
    assert.equal(payload.items[2].type, 'other');
    assert.equal(payload.counts.other, 1);
  } finally {
    globalThis.fetch = previous;
  }
});

test('jin10 oil rig data exposes bearish gold and silver impact label', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: 200,
    message: 'OK',
    data: [{ type: 'data', data: {
      data_id: 1182694, indicator_id: 951, pub_time: '2026-08-01 01:00', country: '美国',
      star: 3, indicator_name: '当周石油钻井总数', previous: '450', actual: '451', unit: '口',
      affect: 1, show_affect: 1,
    } }],
  }), { status: 200 });
  try {
    const response = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-08-01') });
    const payload = await response.json();
    assert.equal(payload.items[0].impact_label, '利空 金银');
    assert.equal(payload.items[0].impact_direction, 'bearish');
    assert.deepEqual(payload.items[0].affected_assets, ['gold', 'silver']);
  } finally {
    globalThis.fetch = previous;
  }
});

test('unverified or unreleased oil rig impacts stay hidden', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: 200,
    message: 'OK',
    data: [
      { type: 'data', data: { data_id: 2, indicator_id: 951, pub_time: '2026-08-08 01:00', indicator_name: '当周石油钻井总数', affect: 1, show_affect: 1, actual: null } },
      { type: 'data', data: { data_id: 3, indicator_id: 951, pub_time: '2026-08-15 01:00', indicator_name: '当周石油钻井总数', affect: 2, show_affect: 1, actual: '449' } },
    ],
  }), { status: 200 });
  try {
    const response = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?start_date=2026-08-08&end_date=2026-08-15') });
    const payload = await response.json();
    for (const item of payload.items) {
      assert.equal(item.impact_label, null);
      assert.equal(item.impact_direction, null);
      assert.deepEqual(item.affected_assets, []);
    }
  } finally {
    globalThis.fetch = previous;
  }
});

test('jin10 calendar sync requires token and persists through D1', async () => {
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31&sync=1', {
    headers: { authorization: 'Bearer test-sync-token' },
  });
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ status: 200, message: 'OK', data: [] }), { status: 200 });
  try {
    const unauthorized = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31&sync=1'), env: { JIN10_SYNC_TOKEN: 'test-sync-token' } });
    assert.equal(unauthorized.status, 401);

    const dbCalls = [];
    const db = { prepare(sql) { const stmt = { args: [], bind(...args) { stmt.args = args; return stmt; }, async run() { dbCalls.push({ sql, args: stmt.args }); return { meta: { changes: 1 } }; }, async first() { return null; } }; return stmt; } };
    const response = await handler({ request, env: { JIN10_SYNC_TOKEN: 'test-sync-token', DB: db } });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.sync.items_upserted, 0);
    assert.ok(dbCalls.some((call) => /CREATE TABLE IF NOT EXISTS jin10_calendar_items/i.test(call.sql)));
  } finally {
    globalThis.fetch = previous;
  }
});

test('jin10 calendar proxy returns a controlled upstream failure', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response('', { status: 502 });
  try {
    const response = await handler({ request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31') });
    const payload = await response.json();
    assert.equal(response.status, 502);
    assert.equal(payload.error, 'jin10 upstream unavailable');
  } finally {
    globalThis.fetch = previous;
  }
});
