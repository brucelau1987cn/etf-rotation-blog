import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('functions/api/v1/tradingview.js', new URL('../', import.meta.url)).pathname).href;
const { onRequestPost } = await import(moduleUrl);

const makeDb = () => {
  const rows = new Map();
  const keyOf = (tradeDate, symbol, cycle, signal) => `${tradeDate}|${symbol}|${cycle}|${signal}`;

  const handle = (sql, args = []) => {
    const text = String(sql);
    if (/CREATE TABLE|CREATE INDEX/i.test(text)) {
      return { kind: 'ok', value: { meta: { changes: 0 } } };
    }
    if (/INSERT OR IGNORE/i.test(text)) {
      const [tradeDate, symbol, cycle, signal, trigger, received, eventId, label, name, exchange, triggerPrice, triggerPriceSource] = args;
      const key = keyOf(tradeDate, symbol, cycle, signal);
      if (rows.has(key)) return { kind: 'ok', value: { meta: { changes: 0 } } };
      rows.set(key, {
        trade_date: tradeDate,
        symbol,
        cycle_code: cycle,
        signal,
        trigger_time_utc: trigger,
        received_at: received,
        event_id: eventId,
        label,
        instrument_name: name,
        exchange,
        trigger_price: triggerPrice ?? null,
        trigger_price_source: triggerPriceSource ?? null,
      });
      return { kind: 'ok', value: { meta: { changes: 1 } } };
    }
    if (/SELECT .* FROM rolling_signals[\s\S]*WHERE trade_date = \? AND symbol = \? AND cycle_code = \? AND signal = \?/i.test(text)
      || (/SELECT/i.test(text) && /FROM rolling_signals/i.test(text) && args.length === 4)) {
      const [tradeDate, symbol, cycle, signal] = args;
      return { kind: 'first', value: rows.get(keyOf(tradeDate, symbol, cycle, signal)) || null };
    }
    if (/FROM rolling_signals/i.test(text) && args.length === 2) {
      const [symbol, tradeDate] = args;
      return {
        kind: 'all',
        value: { results: [...rows.values()].filter(r => r.symbol === symbol && r.trade_date === tradeDate) },
      };
    }
    return { kind: 'ok', value: { meta: { changes: 0 } } };
  };

  return {
    prepare(sql) {
      let boundArgs = [];
      const stmt = {
        bind(...args) {
          boundArgs = args;
          return stmt;
        },
        async run() {
          const out = handle(sql, boundArgs);
          return out.value;
        },
        async first() {
          const out = handle(sql, boundArgs);
          return out.kind === 'first' ? out.value : null;
        },
        async all() {
          const out = handle(sql, boundArgs);
          return out.kind === 'all' ? out.value : { results: [] };
        },
      };
      return stmt;
    },
    _rows: rows,
  };
};

test('tradingview webhook fails 500 when TRADINGVIEW_WEBHOOK_TOKEN is missing', async () => {
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: 'foo', cycle_code: 'PRE', signal: 'BUY' }),
  });
  const res = await onRequestPost({ request: req, env: {} });
  assert.equal(res.status, 500);
  assert.equal((await res.json()).error, 'TRADINGVIEW_WEBHOOK_TOKEN missing on server');
});

test('tradingview webhook accepts valid token and stores first write in D1', async () => {
  const token = 'test_secret_token_123';
  const db = makeDb();
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '600021', cycle_code: 'PRE', signal: 'BUY' }),
  });
  const res = await onRequestPost({ request: req, env: { TRADINGVIEW_WEBHOOK_TOKEN: token, DB: db } });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.success, true);
  assert.equal(body.inserted, true);
  assert.equal(body.storage, 'd1');
  assert.equal(db._rows.size, 1);
});

test('tradingview webhook fails closed when DB is missing', async () => {
  const token = 'test_secret_token_123';
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '01378', cycle_code: '2h', signal: 'BUY' }),
  });
  const res = await onRequestPost({ request: req, env: { TRADINGVIEW_WEBHOOK_TOKEN: token } });
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, 'DB missing on server');
});

test('tradingview webhook normalizes market suffixes before writing D1 rows', async () => {
  const token = 'test_secret_token_123';
  const db = makeDb();
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '600021.SH', cycle_code: '2h', signal: 'BUY' }),
  });
  const res = await onRequestPost({ request: req, env: { TRADINGVIEW_WEBHOOK_TOKEN: token, DB: db } });
  assert.equal(res.status, 200);
  const row = [...db._rows.values()][0];
  assert.equal(row.symbol, '600021');
  assert.equal(row.cycle_code, '2h');
});

test('same day same node is locked after first write', async () => {
  const token = 'test_secret_token_123';
  const db = makeDb();
  const ntfyCalls = [];
  const fetchMock = async (url, options) => {
    ntfyCalls.push({ url: String(url), options });
    return new Response('{"id":"ntfy-test-id"}', { status: 200 });
  };
  const env = {
    TRADINGVIEW_WEBHOOK_TOKEN: token,
    DB: db,
    NTFY_PUSH_URL: 'https://push.example.test/secret-topic',
  };
  const first = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL',
        event_id: 'first', trigger_time_utc: '2026-07-28T02:15:07.359Z',
        price: 76.23,
      }),
    }),
    env,
    fetch: fetchMock,
  });
  const second = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL',
        event_id: 'second', trigger_time_utc: '2026-07-28T03:15:07.359Z',
        price: 80.00,
      }),
    }),
    env,
    fetch: fetchMock,
  });
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  const body1 = await first.json();
  const body2 = await second.json();
  assert.equal(body1.inserted, true);
  assert.equal(body2.inserted, false);
  assert.equal(body2.event_id, 'first');
  assert.equal(db._rows.size, 1);
  assert.equal(ntfyCalls.length, 1);
});

test('first D1 insert forwards the signal to ntfy with the mobile template', async () => {
  const token = 'test_secret_token_123';
  const calls = [];
  const fetchMock = async (url, options) => {
    calls.push(new Request(url, options));
    return new Response(JSON.stringify({ id: 'ntfy-test-id' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  const response = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token,
        symbol: '002173',
        instrument_name: '创新医疗',
        cycle_code: '6.5h',
        signal: 'BUY',
        event_id: 'buy-first',
        trigger_time_utc: '2026-07-28T07:31:00.915Z',
        price: 12.34,
      }),
    }),
    env: {
      TRADINGVIEW_WEBHOOK_TOKEN: token,
      DB: makeDb(),
      NTFY_PUSH_URL: 'https://push.example.test/secret-topic',
    },
    fetch: fetchMock,
  });

  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://push.example.test/');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers.get('content-type'), 'application/json');
  assert.deepEqual(await calls[0].json(), {
    topic: 'secret-topic',
    title: '多方信号｜创新医疗 002173',
    message: '时间：2026-07-28 15:31:00\n节点：6.5小时\n方向：多方信号\n信号点股价：¥12.34',
    priority: 4,
    tags: ['chart_with_upwards_trend'],
    click: 'https://etf.peekabo.cc/rolling/',
  });
});

test('ntfy title resolves the instrument name from Pages assets when the webhook omits it', async () => {
  const token = 'test_secret_token_123';
  const calls = [];
  const fetchMock = async (url, options) => {
    calls.push(new Request(url, options));
    return new Response('{"id":"ntfy-test-id"}', { status: 200 });
  };
  const assets = {
    async fetch(request) {
      assert.equal(new URL(request.url).pathname, '/data/a-rolling-instruments.json');
      return Response.json({
        instruments: [{ instrument_name: '德福科技', symbol: '301511' }],
      });
    },
  };

  const response = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token,
        symbol: '301511',
        cycle_code: '15m',
        signal: 'SELL',
        event_id: 'sell-first',
        trigger_time_utc: '2026-07-28T02:15:07.359Z',
        price: 76.23,
      }),
    }),
    env: {
      TRADINGVIEW_WEBHOOK_TOKEN: token,
      DB: makeDb(),
      NTFY_PUSH_URL: 'https://push.example.test/secret-topic',
      ASSETS: assets,
    },
    fetch: fetchMock,
  });

  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  const notification = await calls[0].json();
  assert.equal(notification.title, '空方信号｜德福科技 301511');
  assert.equal(notification.message, '时间：2026-07-28 10:15:07\n节点：15分钟\n方向：空方信号\n信号点股价：¥76.23');
});

test('ntfy failure is delegated with waitUntil and does not fail D1 ingestion', async () => {
  const token = 'test_secret_token_123';
  const pending = [];
  let fetchCalls = 0;
  const originalWarn = console.warn;
  console.warn = () => {};
  try {
    const response = await onRequestPost({
      request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          webhook_token: token,
          symbol: '600021',
          instrument_name: '上海电力',
          cycle_code: '2h',
          signal: 'BUY',
          event_id: 'wait-until-first',
          trigger_time_utc: '2026-07-28T01:30:00.000Z',
          price: 14.60,
        }),
      }),
      env: {
        TRADINGVIEW_WEBHOOK_TOKEN: token,
        DB: makeDb(),
        NTFY_PUSH_URL: 'https://push.example.test/secret-topic',
      },
      fetch: async (url, options) => {
        fetchCalls += 1;
        new Request(url, options);
        return new Response('upstream unavailable', { status: 503 });
      },
      waitUntil(promise) {
        pending.push(promise);
      },
    });

    assert.equal(response.status, 200);
    assert.equal((await response.json()).inserted, true);
    assert.equal(pending.length, 1);
    assert.equal(await pending[0], false);
    assert.equal(fetchCalls, 1);
  } finally {
    console.warn = originalWarn;
  }
});


test('tradingview webhook stores webhook-provided trigger price into D1', async () => {
  const token = 'test_secret_token_123';
  const db = makeDb();
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      webhook_token: token,
      symbol: '301511',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: '2026-07-28T02:15:07.359Z',
      price: 76.23,
      price_source: 'tv-close',
    }),
  });
  const res = await onRequestPost({ request: req, env: { TRADINGVIEW_WEBHOOK_TOKEN: token, DB: db } });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.inserted, true);
  assert.equal(body.trigger_price, 76.23);
  assert.equal(body.trigger_price_source, 'tv-close');
  const row = [...db._rows.values()][0];
  assert.equal(row.trigger_price, 76.23);
  assert.equal(row.trigger_price_source, 'tv-close');
});

test('tradingview webhook falls back to edge 1m kline when price is omitted', async () => {
  const token = 'test_secret_token_123';
  const db = makeDb();
  const fetchImpl = async (url) => {
    const value = String(url);
    if (value.includes('/api/public/v1/kline')) {
      return Response.json({
        status: 'ok',
        source: 'sina',
        bar: { minute: '2026-07-28 10:15', close: 76.23, source: 'sina-m1' },
      });
    }
    return new Response('{}', { status: 404 });
  };
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      webhook_token: token,
      symbol: '301511',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: '2026-07-28T02:15:07.359Z',
    }),
  });
  const res = await onRequestPost({
    request: req,
    env: { TRADINGVIEW_WEBHOOK_TOKEN: token, DB: db },
    fetch: fetchImpl,
  });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.inserted, true);
  assert.equal(body.trigger_price, 76.23);
  assert.equal(body.trigger_price_source, 'sina-m1');
  const row = [...db._rows.values()][0];
  assert.equal(row.trigger_price, 76.23);
  assert.equal(row.trigger_price_source, 'sina-m1');
});


test('ntfy always includes 信号点股价 line even when price missing', async () => {
  const token = 'test_secret_token_123';
  const calls = [];
  const fetchMock = async (url, options) => {
    const value = String(url);
    if (value.includes('/api/public/v1/kline')) {
      return Response.json({ status: 'ok', bar: null });
    }
    calls.push(new Request(url, options));
    return new Response(JSON.stringify({ id: 'ntfy-test-id' }), { status: 200 });
  };
  const response = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token,
        symbol: 'TSLA',
        instrument_name: '特斯拉',
        cycle_code: '2h',
        signal: 'BUY',
        event_id: 'no-price',
        trigger_time_utc: '2026-07-28T01:30:00.000Z',
      }),
    }),
    env: {
      TRADINGVIEW_WEBHOOK_TOKEN: token,
      DB: makeDb(),
      NTFY_PUSH_URL: 'https://push.example.test/secret-topic',
    },
    fetch: fetchMock,
  });
  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  const notification = await calls[0].json();
  assert.match(notification.message, /信号点股价：暂无/);
});
