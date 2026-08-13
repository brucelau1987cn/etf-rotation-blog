import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const libUrl = pathToFileURL(new URL('functions/_lib/a-rolling.js', new URL('../', import.meta.url)).pathname).href;
const { projectUpstream } = await import(libUrl);
const apiUrl = pathToFileURL(new URL('functions/api/public/v1/rolling-signals.js', new URL('../', import.meta.url)).pathname).href;
const { handleRollingSignals } = await import(apiUrl);
const instrumentsUrl = pathToFileURL(new URL('functions/_lib/rolling-instruments.js', new URL('../', import.meta.url)).pathname).href;
const { seedRollingInstrumentsIfEmpty } = await import(instrumentsUrl);

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
          const args = bound._args;
          const symbols = args.filter(a => typeof a === 'string' && !/^\d{4}-\d{2}-\d{2}$/.test(a));
          const tradeDate = args.find(a => typeof a === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(a));
          return {
            results: rows.filter(r => symbols.includes(r.symbol) && (!tradeDate || r.trade_date === tradeDate)),
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

test('today D1 rows override older static LKG rows for the same node (cross-day no shadow)', async () => {
  const tradeDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const todayReceived = new Date().toISOString();
  const db = makeDb([
    {
      trade_date: tradeDate,
      symbol: '06809',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-today-15m',
      label: '15m',
    },
    {
      trade_date: tradeDate,
      symbol: '06809',
      cycle_code: '10m',
      signal: 'SELL',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-today-10m',
      label: '10m',
    },
  ]);
  const payload = lkgPayload('06809');
  payload.timeline = [
    { type: 'SELL', code: '15m', triggered_at: '2026-07-30T07:15:08.097Z', received_at: '2026-07-30T07:15:08.097Z', event_id: 'evt-old-15m', label: '15m' },
    { type: 'SELL', code: '10m', triggered_at: '2026-08-03T01:55:03.273Z', received_at: '2026-08-03T01:55:03.273Z', event_id: 'evt-old-10m', label: '10m' },
  ];
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=06809');
  const body = await (await handleRollingSignals(request, assetEnv(payload, db))).json();
  assert.equal(body.delivery.state, 'live');
  assert.equal(body.storage, 'd1');
  const byCode = Object.fromEntries(body.timeline.map(item => [item.code, item]));
  // Today's first-write row must win; the older static LKG rows must not shadow it.
  assert.equal(byCode['15m'].event_id, 'evt-today-15m');
  assert.equal(byCode['10m'].event_id, 'evt-today-10m');
});

test('older D1 history does not shadow today rows but fills nodes missing today', async () => {
  const tradeDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const todayReceived = new Date().toISOString();
  const db = makeDb([
    // Historical D1 rows (earlier days) for the same nodes.
    {
      trade_date: '2026-08-03',
      symbol: '06809',
      cycle_code: '10m',
      signal: 'SELL',
      trigger_time_utc: '2026-08-03T01:55:03.273Z',
      received_at: '2026-08-03T01:55:03.273Z',
      event_id: 'evt-hist-10m',
      label: '10m',
    },
    {
      trade_date: '2026-07-30',
      symbol: '06809',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: '2026-07-30T07:15:08.097Z',
      received_at: '2026-07-30T07:15:08.097Z',
      event_id: 'evt-hist-15m',
      label: '15m',
    },
    // Today's first-write rows.
    {
      trade_date: tradeDate,
      symbol: '06809',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-today-15m',
      label: '15m',
    },
    {
      trade_date: tradeDate,
      symbol: '06809',
      cycle_code: '10m',
      signal: 'SELL',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-today-10m',
      label: '10m',
    },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbol=06809');
  const body = await (await handleRollingSignals(request, assetEnv(lkgPayload('06809'), db))).json();
  const byCode = Object.fromEntries(body.timeline.map(item => [item.code, item]));
  assert.equal(byCode['15m'].event_id, 'evt-today-15m');
  assert.equal(byCode['10m'].event_id, 'evt-today-10m');
});

test('batch symbols returns one board per requested symbol without extra isolate hops', async () => {
  const tradeDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const todayReceived = new Date().toISOString();
  const db = makeDb([
    {
      trade_date: tradeDate,
      symbol: '301511',
      cycle_code: '15m',
      signal: 'SELL',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-511',
      label: '15m',
    },
    {
      trade_date: tradeDate,
      symbol: '06809',
      cycle_code: '1h',
      signal: 'BUY',
      trigger_time_utc: todayReceived,
      received_at: todayReceived,
      event_id: 'evt-809',
      label: '1h',
    },
  ]);
  const payloads = {
    '/data/a-rolling-signals-301511.json': lkgPayload('301511'),
    '/data/a-rolling-signals-06809.json': lkgPayload('06809'),
  };
  const env = {
    ASSETS: {
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        const payload = payloads[path];
        if (!payload) return new Response('missing', { status: 404 });
        return new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } });
      },
    },
    DB: db,
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbols=301511,06809');
  const response = await handleRollingSignals(request, env);
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.schema_version, 'a-rolling-energy-batch-v1');
  assert.equal(body.boards.length, 2);
  const bySymbol = Object.fromEntries(body.boards.map(item => [item.instrument.symbol, item]));
  assert.ok(bySymbol['301511'].timeline.some(item => item.event_id === 'evt-511'));
  assert.ok(bySymbol['06809'].timeline.some(item => item.event_id === 'evt-809'));
});

test('batch LKG asset reads start in parallel', async () => {
  const started = [];
  const payloads = {
    '/data/a-rolling-signals-301511.json': lkgPayload('301511'),
    '/data/a-rolling-signals-06809.json': lkgPayload('06809'),
  };
  const env = {
    ASSETS: {
      fetch: async (request) => {
        started.push(Date.now());
        await new Promise(resolve => setTimeout(resolve, 40));
        const path = new URL(request.url).pathname;
        const payload = payloads[path];
        if (!payload) return new Response('missing', { status: 404 });
        return new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } });
      },
    },
    DB: makeDb([]),
  };
  const t0 = Date.now();
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbols=301511,06809');
  const response = await handleRollingSignals(request, env);
  const elapsed = Date.now() - t0;
  const body = await response.json();
  assert.equal(body.boards.length, 2);
  assert.equal(started.length, 2);
  assert.ok(Math.abs(started[1] - started[0]) < 20, `LKG fetches serialized: ${started.join(',')}`);
  assert.ok(elapsed < 70, `batch still serial (${elapsed}ms)`);
});

test('batch initializes rolling instruments once before parallel board loads', async () => {
  let countReads = 0;
  const db = makeDb([]);
  const originalPrepare = db.prepare.bind(db);
  db.prepare = (sql) => {
    const statement = originalPrepare(sql);
    if (/SELECT COUNT\(\*\) AS n FROM rolling_instruments/i.test(String(sql))) {
      statement.first = async () => {
        countReads += 1;
        return { n: 1 };
      };
    }
    return statement;
  };
  const env = assetEnv(lkgPayload('301511'), db);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbols=301511,06809');
  const response = await handleRollingSignals(request, env);
  assert.equal(response.status, 200);
  assert.equal(countReads, 1, `rolling instruments seeded ${countReads} times`);
});

test('rolling instrument seed tolerates concurrent duplicate inserts', async () => {
  const inserts = [];
  const db = {
    prepare(sql) {
      const text = String(sql);
      return {
        bind(...args) { this.args = args; return this; },
        async first() { return { n: 0 }; },
        async run() {
          if (/INSERT/i.test(text)) inserts.push(text);
          return { meta: { changes: 1 } };
        },
      };
    },
  };
  await Promise.all([seedRollingInstrumentsIfEmpty(db), seedRollingInstrumentsIfEmpty(db)]);
  assert.ok(inserts.length > 0);
  assert.ok(inserts.every(sql => /INSERT OR IGNORE INTO rolling_instruments/i.test(sql)));
});

test('batch omits failed snapshots and disables cache on partial failure', async () => {
  const env = {
    ASSETS: {
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        if (path.endsWith('301511.json')) {
          return new Response(JSON.stringify(lkgPayload('301511')), { headers: { 'content-type': 'application/json' } });
        }
        return new Response('missing', { status: 404 });
      },
    },
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbols=301511,06809');
  const response = await handleRollingSignals(request, env);
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(body.boards.map(item => item.instrument.symbol), ['301511']);
  assert.deepEqual(body.errors, [{ symbol: '06809', error: 'rolling signal snapshot unavailable' }]);
});

test('batch returns 503 no-store when every snapshot fails', async () => {
  const env = { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/rolling-signals?symbols=301511,06809');
  const response = await handleRollingSignals(request, env);
  const body = await response.json();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(body.ok, false);
  assert.equal(body.boards.length, 0);
  assert.equal(body.errors.length, 2);
});
