import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const root = new URL('../', import.meta.url);
const fixture = JSON.parse(await readFile(new URL('public/data/a-rolling-signals.json', root), 'utf8'));
const moduleUrl = pathToFileURL(new URL('functions/api/public/v1/rolling-signals.js', root).pathname).href;
const { handleRollingSignals } = await import(moduleUrl);
const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals');
const assets = { fetch: async () => new Response(JSON.stringify(fixture), { status: 200, headers: { 'content-type': 'application/json' } }) };
const upstream = () => ({
  generated_at: '2026-07-23T04:00:00Z', data_as_of: '2026-07-23T03:59:00Z', private_note: 'hidden', instrument: fixture.instrument,
  cycles: fixture.cycles.map((row, index) => ({ cycle_code: row.cycle_code, timeframe_minutes: row.timeframe_minutes, buy_state: 'BUY', buy_triggered_at: '2026-07-23T01:30:00Z', internal_id: index })), sell_alerts: [],
});
const withFetch = async (implementation, fn) => { const previous = globalThis.fetch; globalThis.fetch = implementation; try { await fn(); } finally { globalThis.fetch = previous; } };

test('unconfigured upstream returns validated 34-cycle LKG', async () => {
  const response = await handleRollingSignals(request, { ASSETS: assets });
  const body = await response.json();
  assert.equal(response.status, 200); assert.equal(response.headers.get('x-rolling-delivery'), 'lkg');
  assert.equal(body.schema_version, 'a-rolling-energy-v2'); assert.equal(body.delivery.state, 'lkg'); assert.equal(body.cycles.length, 34);
});

test('valid upstream is strictly projected as a continuous single run', async () => {
  await withFetch(async () => new Response(JSON.stringify(upstream()), { headers: { 'content-type': 'application/json' } }), async () => {
    const response = await handleRollingSignals(request, { ASSETS: assets, A_ROLLING_UPSTREAM_URL: 'https://signals.example.test/public' });
    const body = await response.json();
    assert.equal(body.delivery.state, 'live'); assert.equal(body.transmission.basis, 'single_run'); assert.equal(body.transmission.current_cycle_code, 'G');
    assert.equal(body.cycles.length, 34); assert.equal(body.cycles[22].timeframe_minutes, 370); assert.equal('internal_id' in body.cycles[0], false); assert.equal('private_note' in body, false);
  });
});

test('broken transmission path degrades to LKG without exposing errors', async () => {
  const broken = upstream(); broken.cycles[4].buy_state = 'INACTIVE'; broken.cycles[4].buy_triggered_at = null;
  await withFetch(async () => new Response(JSON.stringify(broken), { headers: { 'content-type': 'application/json' } }), async () => {
    const response = await handleRollingSignals(request, { ASSETS: assets, A_ROLLING_UPSTREAM_URL: 'https://signals.example.test/public' });
    const body = await response.json();
    assert.equal(body.delivery.state, 'lkg'); assert.equal(body.delivery.reason, '上游暂不可用或数据未通过校验'); assert.doesNotMatch(JSON.stringify(body), /contiguous|example\.test/);
  });
});

test('missing static snapshot fails closed', async () => {
  const response = await handleRollingSignals(request, { ASSETS: { fetch: async () => new Response('missing', { status: 404, headers: { 'content-type': 'text/plain' } }) } });
  assert.equal(response.status, 503); assert.deepEqual(await response.json(), { error: 'rolling signal snapshot unavailable' });
});
