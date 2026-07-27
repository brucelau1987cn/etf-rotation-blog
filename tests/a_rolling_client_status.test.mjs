import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const statusUrl = pathToFileURL(new URL('public/js/a-rolling-status.js', new URL('../', import.meta.url)).pathname).href;
await import(statusUrl);
const { summarize } = globalThis.ARollingDelivery;

test('rolling status reports all-live only when every configured instrument is fresh live data', () => {
  assert.deepEqual(summarize([
    { freshness: 'fresh', delivery: { state: 'live' } },
    { freshness: 'fresh', delivery: { state: 'live' } },
  ], 2), { state: 'live', text: '● 2/2 标的实时同步' });

  assert.deepEqual(summarize([
    { freshness: 'fresh', delivery: { state: 'live' } },
    { freshness: 'stale', delivery: { state: 'lkg' } },
  ], 2), { state: 'partial', text: '⚠ 1/2 实时 · 1个LKG' });

  assert.deepEqual(summarize([
    { freshness: 'stale', delivery: { state: 'lkg' } },
  ], 2), { state: 'lkg', text: '⚠ 0/2 实时 · 1个LKG · 1个不可用' });
});
