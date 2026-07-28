import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('functions/api/v1/tradingview.js', new URL('../', import.meta.url)).pathname).href;
const { onRequestPost } = await import(moduleUrl);

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

test('tradingview webhook accepts valid token and stores signal to KV', async () => {
  const token = 'test_secret_token_123';
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '600021', cycle_code: 'PRE', signal: 'BUY' }),
  });

  const kvStore = new Map();
  const env = {
    TRADINGVIEW_WEBHOOK_TOKEN: token,
    ROLLING_KV: {
      get: async (key) => kvStore.get(key) || null,
      put: async (key, val) => kvStore.set(key, val),
    },
  };

  const res = await onRequestPost({ request: req, env });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.success, true);
  assert.equal(kvStore.has('signal:600021:PRE:BUY'), true);
  assert.equal(kvStore.has('latest:600021'), true);
  assert.equal(kvStore.has('index:600021'), true);
  assert.equal(kvStore.has('timeline:600021'), true);
  const index = JSON.parse(kvStore.get('index:600021'));
  assert.equal(index.symbol, '600021');
  assert.equal(index.entries.length, 1);
  assert.equal(index.entries[0].key, 'signal:600021:PRE:BUY');
  const timeline = JSON.parse(kvStore.get('timeline:600021'));
  assert.equal(timeline.events.length, 1);
  assert.equal(timeline.events[0].code, 'PRE');
});

test('tradingview webhook fails closed when ROLLING_KV is missing', async () => {
  const token = 'test_secret_token_123';
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '01378', cycle_code: '2h', signal: 'BUY' }),
  });

  const res = await onRequestPost({ request: req, env: { TRADINGVIEW_WEBHOOK_TOKEN: token } });
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, 'ROLLING_KV missing on server');
});

test('tradingview webhook normalizes market suffixes before writing KV keys', async () => {
  const token = 'test_secret_token_123';
  const kvStore = new Map();
  const req = new Request('https://etf.peekabo.cc/api/v1/tradingview', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ webhook_token: token, symbol: '600021.SH', cycle_code: '2h', signal: 'BUY' }),
  });
  const env = {
    TRADINGVIEW_WEBHOOK_TOKEN: token,
    ROLLING_KV: { get: async key => kvStore.get(key) || null, put: async (key, value) => kvStore.set(key, value) },
  };
  const res = await onRequestPost({ request: req, env });
  assert.equal(res.status, 200);
  assert.equal(kvStore.has('signal:600021:2h:BUY'), true);
  assert.equal(JSON.parse(kvStore.get('latest:600021')).symbol, '600021');
  assert.equal(JSON.parse(kvStore.get('index:600021')).entries[0].cycle_code, '2h');
});

test('tradingview webhook upserts index entries without duplicates', async () => {
  const token = 'test_secret_token_123';
  const kvStore = new Map([
    ['index:301511', JSON.stringify({
      symbol: '301511',
      entries: [{ key: 'signal:301511:10m:SELL', cycle_code: '10m', signal: 'SELL' }],
    })],
  ]);
  const env = {
    TRADINGVIEW_WEBHOOK_TOKEN: token,
    ROLLING_KV: {
      get: async key => kvStore.get(key) || null,
      put: async (key, value) => kvStore.set(key, value),
    },
  };
  const first = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL' }),
    }),
    env,
  });
  const second = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL' }),
    }),
    env,
  });
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  const index = JSON.parse(kvStore.get('index:301511'));
  assert.deepEqual(index.entries.map(item => item.key), [
    'signal:301511:10m:SELL',
    'signal:301511:15m:SELL',
  ]);
  const timeline = JSON.parse(kvStore.get('timeline:301511'));
  assert.deepEqual(timeline.events.map(item => item.code), ['15m']);
});
