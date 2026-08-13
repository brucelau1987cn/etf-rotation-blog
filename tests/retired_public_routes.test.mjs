import assert from 'node:assert/strict';
import test from 'node:test';
import { onRequestGet as thsGet } from '../functions/api/public/v1/ths/[[path]].js';
import { onRequestGet as statusGet } from '../functions/api/public/v1/subscription/status.js';
import { onRequestGet as indicatorHistoryGet } from '../functions/api/public/v1/jin10-indicator-history.js';

test('public THS proxy is retired', async () => {
  const response = await thsGet({ request: new Request('https://etf.peekabo.cc/api/public/v1/ths/ping') });
  assert.equal(response.status, 410);
  const body = await response.json();
  assert.equal(body.status, 'gone');
});

test('public subscription status is retired', async () => {
  const response = await statusGet({ request: new Request('https://etf.peekabo.cc/api/public/v1/subscription/status') });
  assert.equal(response.status, 410);
  const body = await response.json();
  assert.equal(body.status, 'gone');
});

test('public jin10 indicator history is retired', async () => {
  const response = await indicatorHistoryGet({
    request: new Request('https://etf.peekabo.cc/api/public/v1/jin10-indicator-history?id=75'),
  });
  assert.equal(response.status, 410);
  const body = await response.json();
  assert.equal(body.status, 'gone');
});
