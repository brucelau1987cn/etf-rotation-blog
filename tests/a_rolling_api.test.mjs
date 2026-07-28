import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const libUrl = pathToFileURL(new URL('functions/_lib/a-rolling.js', new URL('../', import.meta.url)).pathname).href;
const { projectUpstream } = await import(libUrl);
const apiUrl = pathToFileURL(new URL('functions/api/public/v1/rolling-signals.js', new URL('../', import.meta.url)).pathname).href;
const { handleRollingSignals } = await import(apiUrl);

const lkgPayload = (symbol = '01378') => ({
  schema_version: 'a-rolling-energy-v4',
  generated_at: '2026-07-27T13:04:03Z',
  data_as_of: '2026-07-24T01:30:00Z',
  freshness: 'fresh',
  stale_after_seconds: 900,
  delivery: { state: 'live', reason: null },
  instrument: { instrument_name: symbol, exchange: 'TEST', symbol },
  transmission: { state: 'transmitting' },
  timeline: [{ type: 'BUY', code: '1h', triggered_at: '2026-07-24T01:30:00Z', label: '1h' }],
});

const assetEnv = (payload, kv = null) => ({
  ASSETS: { fetch: async () => new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } }) },
  ...(kv ? { ROLLING_KV: kv } : {}),
});

test('projectUpstream formats buy & sell chains on-demand without placeholders', () => {
  const upstream = {
    data_as_of: '2026-07-24T12:00:00Z',
    cycles: [
      { cycle_code: '2h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:30:00Z' },
      { cycle_code: '2.5h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:45:00Z' },
    ],
    sell_chain: {
      nodes: [
        { code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }
      ]
    }
  };

  const res = projectUpstream(upstream);
  assert.equal(res.schema_version, 'a-rolling-energy-v4');
  assert.equal(res.cycles.length, 2);
  assert.equal(res.sell_chain.nodes.length, 1);
});

test('sell chain is hidden if there are no buy signals', () => {
  const upstream = {
    cycles: [],
    sell_chain: {
      nodes: [
        { code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }
      ]
    }
  };

  const res = projectUpstream(upstream);
  assert.equal(res.cycles.length, 0);
  assert.equal(res.sell_chain.nodes.length, 0);
});

test('projectUpstream marks an old data_as_of snapshot stale', () => {
  const res = projectUpstream(
    { ...lkgPayload(), timeline: lkgPayload().timeline },
    '2026-07-28T00:00:00Z',
    900,
  );
  assert.equal(res.freshness, 'stale');
  assert.equal(res.delivery.state, 'lkg');
});

test('non-default rolling symbol exposes static assets as LKG', async () => {
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=01378');
  const response = await handleRollingSignals(request, assetEnv(lkgPayload()));
  const body = await response.json();
  assert.equal(response.headers.get('x-rolling-delivery'), 'lkg');
  assert.equal(body.delivery.state, 'lkg');
  assert.equal(body.freshness, 'stale');
  assert.ok(Number(body.data_age_seconds) > 900);
  assert.match(body.delivery.reason, /KV|静态|上游/);
});

test('timeline bundle rebuilds live signals with a single get and no list', async () => {
  const receivedAt1 = new Date(Date.now() - 2000).toISOString();
  const receivedAt2 = new Date(Date.now() - 1000).toISOString();
  let listCalls = 0;
  let getCalls = 0;
  const records = new Map([
    ['timeline:01378', JSON.stringify({
      symbol: '01378',
      events: [
        { symbol: '01378', type: 'BUY', code: '2h', label: '2h', triggered_at: receivedAt1, received_at: receivedAt1, event_id: 'evt-1' },
        { symbol: '01378', type: 'SELL', code: '10m', label: '10m', triggered_at: receivedAt2, received_at: receivedAt2, event_id: 'evt-2' },
      ],
    })],
  ]);
  const kv = {
    list: async () => {
      listCalls += 1;
      throw new Error('list must not be used');
    },
    get: async key => {
      getCalls += 1;
      return records.get(key) || null;
    },
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=01378');
  const response = await handleRollingSignals(request, assetEnv(lkgPayload(), kv));
  const body = await response.json();
  assert.equal(response.headers.get('x-rolling-delivery'), 'live');
  assert.deepEqual(body.timeline.map(item => [item.type, item.code]), [['BUY', '2h'], ['SELL', '10m']]);
  assert.equal(body.data_as_of, receivedAt2);
  assert.equal(listCalls, 0);
  assert.equal(getCalls, 1);
});

test('unknown KV-backed symbol never inherits Shanghai Electric metadata', async () => {
  const kv = {
    list: async () => {
      throw new Error('list must not be used');
    },
    get: async key => {
      if (key === 'timeline:NEW1') {
        return JSON.stringify({
          symbol: 'NEW1',
          events: [{
            symbol: 'NEW1', type: 'BUY', code: '1h', label: '1h',
            triggered_at: '2026-07-28T00:00:00Z', received_at: '2026-07-28T00:00:01Z', event_id: 'evt-new',
          }],
        });
      }
      return null;
    },
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=NEW1');
  const response = await handleRollingSignals(request, assetEnv(lkgPayload('600021'), kv));
  const body = await response.json();
  assert.equal(body.instrument.symbol, 'NEW1');
  assert.equal(body.instrument.instrument_name, 'NEW1');
  assert.deepEqual(body.timeline.map(item => [item.type, item.code]), [['BUY', '1h']]);
});

test('index full-record fallback works without per-node gets', async () => {
  const receivedAt1 = '2026-07-28T00:00:01Z';
  const receivedAt2 = '2026-07-28T00:05:01Z';
  let getCalls = 0;
  const kv = {
    list: async () => {
      throw new Error('list must not be used');
    },
    get: async key => {
      getCalls += 1;
      if (key === 'timeline:TSLA') return null;
      if (key === 'index:TSLA') {
        return JSON.stringify({
          symbol: 'TSLA',
          entries: [
            {
              key: 'signal:TSLA:2h:BUY', type: 'BUY', code: '2h', signal: 'BUY', cycle_code: '2h',
              triggered_at: '2026-07-28T00:10:00Z', received_at: receivedAt1, event_id: 'first-received',
            },
            {
              key: 'signal:TSLA:10m:SELL', type: 'SELL', code: '10m', signal: 'SELL', cycle_code: '10m',
              triggered_at: '2026-07-28T00:05:00Z', received_at: receivedAt2, event_id: 'second-received',
            },
          ],
        });
      }
      return null;
    },
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=TSLA');
  const body = await (await handleRollingSignals(request, assetEnv(lkgPayload('TSLA'), kv))).json();
  assert.deepEqual(body.timeline.map(item => item.event_id), ['first-received', 'second-received']);
  assert.equal(body.timeline[0].received_at, receivedAt1);
  assert.equal(getCalls, 2); // timeline miss + index hit
});

test('missing timeline and index returns LKG without scanning all board nodes', async () => {
  let getCalls = 0;
  const kv = {
    list: async () => {
      throw new Error('list must not be used');
    },
    get: async () => {
      getCalls += 1;
      return null;
    },
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=301511');
  const body = await (await handleRollingSignals(request, assetEnv(lkgPayload('301511'), kv))).json();
  assert.equal(body.delivery.state, 'lkg');
  assert.ok(getCalls <= 2);
});
