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
      const [tradeDate, symbol, cycle, signal, trigger, received, eventId, label, name, exchange] = args;
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
  const env = { TRADINGVIEW_WEBHOOK_TOKEN: token, DB: db };
  const first = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL',
        event_id: 'first', trigger_time_utc: '2026-07-28T02:15:07.359Z',
      }),
    }),
    env,
  });
  const second = await onRequestPost({
    request: new Request('https://etf.peekabo.cc/api/v1/tradingview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        webhook_token: token, symbol: '301511', cycle_code: '15m', signal: 'SELL',
        event_id: 'second', trigger_time_utc: '2026-07-28T03:15:07.359Z',
      }),
    }),
    env,
  });
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  const body1 = await first.json();
  const body2 = await second.json();
  assert.equal(body1.inserted, true);
  assert.equal(body2.inserted, false);
  assert.equal(body2.event_id, 'first');
  assert.equal(db._rows.size, 1);
});
