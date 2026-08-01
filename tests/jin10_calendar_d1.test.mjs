import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../functions/_lib/jin10-calendar-d1.js', import.meta.url).pathname).href;
const { deriveAssetSignals, persistJin10Items } = await import(moduleUrl);

const rigItem = {
  type: 'data',
  id: 1182694,
  indicator_id: 951,
  time: '2026-08-01 01:00',
  country: '美国',
  star: 3,
  title: '当周石油钻井总数',
  previous: '450',
  consensus: null,
  actual: '451',
  revised: null,
  unit: '口',
  affect: 1,
  show_affect: 1,
  time_status: null,
  source: null,
};

test('oil rig affect=1 derives bearish gold and silver signals', () => {
  const signals = deriveAssetSignals(rigItem);
  assert.deepEqual(signals.map((item) => [item.symbol, item.direction, item.display_name]), [
    ['GC=F', 'bearish', '黄金期货'],
    ['SI=F', 'bearish', '白银现货'],
  ]);
  assert.equal(signals[1].rolling_signal, 'SELL');
  assert.equal(signals[1].rolling_code, '宏观利空');
  assert.match(signals[1].label, /美国当周石油钻井总数/);
});

test('unreleased oil rig item does not create an asset signal', () => {
  assert.deepEqual(deriveAssetSignals({ ...rigItem, actual: null }), []);
});

test('persistJin10Items upserts calendar records and silver rolling signal', async () => {
  const calls = [];
  const db = {
    prepare(sql) {
      const stmt = {
        args: [],
        bind(...args) { stmt.args = args; return stmt; },
        async run() { calls.push({ sql: String(sql), args: stmt.args }); return { meta: { changes: 1 } }; },
        async first() { return null; },
      };
      return stmt;
    },
  };

  const result = await persistJin10Items(db, [rigItem], '2026-08-01T13:00:00.000Z');
  assert.equal(result.items_upserted, 1);
  assert.equal(result.asset_signals_upserted, 2);
  assert.equal(result.rolling_signals_inserted, 1);
  assert.ok(calls.some((call) => /INSERT INTO jin10_calendar_items/i.test(call.sql)));
  assert.ok(calls.some((call) => /INSERT INTO jin10_asset_signals/i.test(call.sql) && call.args.includes('SI=F')));
  assert.ok(calls.some((call) => /INSERT OR IGNORE INTO rolling_signals/i.test(call.sql) && call.args.includes('宏观利空') && call.args.includes('SI=F')));
});
