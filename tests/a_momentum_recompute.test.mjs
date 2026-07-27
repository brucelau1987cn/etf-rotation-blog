import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import '../public/js/a-momentum-recompute.js';

const { recomputeMomentumRow } = globalThis.AMomentumRecompute;

test('live momentum recompute is idempotent at the snapshot price for all 91 rows', () => {
  const payload = JSON.parse(fs.readFileSync(new URL('../public/data/a-compass-dashboard.json', import.meta.url), 'utf8'));
  assert.equal(payload.all_rows.filter(row => row.slope20 == null).length, 0);
  const changed = payload.all_rows
    .map(row => [row.code, row.status, recomputeMomentumRow(row, Number(row.price)).status])
    .filter(([, before, after]) => before !== after);
  assert.deepEqual(changed, []);
});

test('defense and cash structural statuses survive live recompute', () => {
  for (const status of ['defense', 'cash']) {
    const row = {
      status, price: 100, ret3: 1, ret5: 1, ret10: 1, ret20: 1,
      ma20: 90, ma20_prev: 89, slope20: 1, checks: {},
    };
    assert.equal(recomputeMomentumRow(row, 110).status, status);
  }
});

test('null model inputs disable live status recompute', () => {
  const row = {status: 'core', price: 100, ret3: 1, ret5: 1, ma20: 90, ma20_prev: 89, slope20: null, checks: {momentum: true}};
  assert.equal(recomputeMomentumRow(row, 100).status, 'core');
});
