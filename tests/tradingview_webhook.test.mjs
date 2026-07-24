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
      put: async (key, val) => kvStore.set(key, val),
    },
  };

  const res = await onRequestPost({ request: req, env });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.success, true);
  assert.equal(kvStore.has('signal:600021:PRE:BUY'), true);
  assert.equal(kvStore.has('latest:600021'), true);
});
