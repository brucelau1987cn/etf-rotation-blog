import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../functions/_lib/jin10-calendar-d1.js', import.meta.url).pathname).href;
const { persistJin10Items } = await import(moduleUrl);

test('persistJin10Items inserts calendar items into D1', async () => {
  const dbCalls = [];
  const db = { prepare(sql) { const stmt = { args: [], bind(...args) { stmt.args = args; return stmt; }, async run() { dbCalls.push({ sql, args: stmt.args }); return { meta: { changes: 1 } }; }, async first() { return null; } }; return stmt; } };
  const items = [{ type: 'data', id: 1182694, indicator_id: 951, time: '2026-08-01 01:00', country: '美国', star: 3, title: '当周石油钻井总数', previous: '450', actual: '451', unit: '口', affect: 1, show_affect: 1 }];
  const result = await persistJin10Items(db, items);
  assert.equal(result.items_upserted, 1);
  assert.equal(result.asset_signals_upserted, 0);
  assert.equal(result.rolling_signals_inserted, 0);
  assert.ok(dbCalls.some((c) => c.sql.includes('INSERT INTO jin10_calendar_items')));
  const insertCall = dbCalls.find((c) => c.sql.includes('INSERT INTO jin10_calendar_items'));
  assert.ok(insertCall);
  assert.equal(insertCall.args[7], '当周石油钻井总数');
});