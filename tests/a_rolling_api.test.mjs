import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const root = new URL('../', import.meta.url);
const fixture = JSON.parse(await readFile(new URL('public/data/a-rolling-signals.json', root), 'utf8'));
const moduleUrl = pathToFileURL(new URL('functions/api/public/v1/rolling-signals.js', root).pathname).href;
const { handleRollingSignals } = await import(moduleUrl);

const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals');
const assets = {
  fetch: async () => new Response(JSON.stringify(fixture), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  }),
};

const upstream = () => ({
  generated_at: '2026-07-23T02:00:00Z',
  data_as_of: '2026-07-23T01:59:00Z',
  private_note: 'must not be projected',
  signals: fixture.signals.map(({ source, ...row }) => ({ ...row, latest_signal_at: '2026-07-23T01:58:00Z', internal_id: 7 })),
});

const withFetch = async (implementation, fn) => {
  const previous = globalThis.fetch;
  globalThis.fetch = implementation;
  try { await fn(); } finally { globalThis.fetch = previous; }
};

test('unconfigured upstream returns validated LKG with cache headers', async () => {
  const response = await handleRollingSignals(request, { ASSETS: assets });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('x-rolling-delivery'), 'lkg');
  assert.match(response.headers.get('cache-control'), /s-maxage=30/);
  assert.equal(body.delivery.state, 'lkg');
  assert.equal(body.signals.length, 9);
});

test('valid upstream is strictly projected as live data', async () => {
  await withFetch(async () => new Response(JSON.stringify(upstream()), { headers: { 'content-type': 'application/json' } }), async () => {
    const response = await handleRollingSignals(request, { ASSETS: assets, A_ROLLING_UPSTREAM_URL: 'https://signals.example.test/public' });
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('x-rolling-delivery'), 'live');
    assert.equal(body.delivery.state, 'live');
    assert.equal(body.mode, 'live');
    assert.equal(body.signals.length, 9);
    assert.equal(body.signals[0].source, 'UPSTREAM_PROJECTION');
    assert.equal('internal_id' in body.signals[0], false);
    assert.equal('private_note' in body, false);
  });
});

test('invalid upstream degrades to LKG without exposing internal errors', async () => {
  const broken = upstream();
  broken.signals.pop();
  await withFetch(async () => new Response(JSON.stringify(broken), { headers: { 'content-type': 'application/json' } }), async () => {
    const response = await handleRollingSignals(request, { ASSETS: assets, A_ROLLING_UPSTREAM_URL: 'https://signals.example.test/public' });
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.delivery.state, 'lkg');
    assert.equal(body.delivery.reason, '上游暂不可用或数据未通过校验');
    assert.doesNotMatch(JSON.stringify(body), /incomplete|signals\.pop|example\.test/);
  });
});

test('missing static snapshot fails closed', async () => {
  const response = await handleRollingSignals(request, {
    ASSETS: { fetch: async () => new Response('missing', { status: 404, headers: { 'content-type': 'text/plain' } }) },
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'rolling signal snapshot unavailable' });
});
