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
  transmission: { state: 'transmitting', start_date: '2026-07-21' },
  timeline: [{ type: 'BUY', code: '1h', triggered_at: '2026-07-24T01:30:00Z', label: '1h' }],
});

const makeDb = (rows) => ({
  prepare(sql) {
    const text = String(sql);
    const bound = {
      _args: [],
      bind(...args) {
        bound._args = args;
        return bound;
      },
      async run() { return { meta: { changes: 0 } }; },
      async first() { return null; },
      async all() {
        if (/FROM rolling_signals/i.test(text)) {
          const [symbol, tradeDate] = bound._args;
          return {
            results: rows.filter(r => r.symbol === symbol && r.trade_date === tradeDate),
          };
        }
        return { results: [] };
      },
    };
    return bound;
  },
});

const assetEnv = (payload, db = null) => ({
  ASSETS: { fetch: async () => new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } }) },
  ...(db ? { DB: db } : {}),
});

test('projectUpstream formats buy & sell chains on-demand without placeholders', () => {
  const res = projectUpstream({
    data_as_of: '2026-07-24T12:00:00Z',
    cycles: [
      { cycle_code: '2h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:30:00Z' },
      { cycle_code: '2.5h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:45:00Z' },
    ],
    sell_chain: { nodes: [{ code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }] },
  });
  assert.equal(res.schema_version, 'a-rolling-energy-v4');
  assert.equal(res.cycles.length, 2);
  assert.equal(res.sell_chain.nodes.length, 1);
});

test('sell chain is hidden if there are no buy signals', () => {
  const res = projectUpstream({
    cycles: [],
    sell_chain: { nodes: [{ code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }] },
  });
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

test('static LKG remains available when D1 has no day rows', async () => {
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=01378');
  const body = await (await handleRollingSignals(request, assetEnv(lkgPayload(), makeDb([])))).json();
  assert.ok(body.timeline.length >= 1);
  assert.equal(body.timeline[0].code, '1h');
});

test('D1 day board is preferred and marked live without KV', async () => {
  const tradeDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const receivedAt = new Date().toISOString();
  const db = makeDb([
    {
      trade_date: tradeDate,
      symbol: '301511',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: receivedAt,
      received_at: receivedAt,
      event_id: 'evt-15m',
      label: '15m',
    },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=301511');
  const body = await (await handleRollingSignals(request, assetEnv(lkgPayload('301511'), db))).json();
  assert.equal(body.delivery.state, 'live');
  assert.equal(body.storage, 'd1');
  assert.ok(body.timeline.some(item => item.code === '15m' && item.type === 'SELL'));
});

test('unknown D1 symbol keeps its own instrument identity', async () => {
  const tradeDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const db = makeDb([
    {
      trade_date: tradeDate,
      symbol: 'NEW1',
      cycle_code: '1h',
      signal: 'BUY',
      trigger_time_utc: '2026-07-28T00:00:00Z',
      received_at: '2026-07-28T00:00:01Z',
      event_id: 'evt-new',
      label: '1h',
    },
  ]);
  const payload = lkgPayload('NEW1');
  payload.timeline = [];
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=NEW1');
  const body = await (await handleRollingSignals(request, assetEnv(payload, db))).json();
  assert.equal(body.instrument.symbol, 'NEW1');
  assert.deepEqual(body.timeline.map(item => [item.type, item.code]), [['BUY', '1h']]);
});
