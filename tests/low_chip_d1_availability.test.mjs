import assert from 'node:assert/strict';
import test from 'node:test';
import { onRequest } from '../functions/api/public/v1/low-chip-metrics.js';

// D1 boundary fake: capture actual bound values and return the same stored rows.
class DB {
  constructor() { this.rows = []; }
  prepare(sql) {
    const db = this;
    return {
      sql, values: [],
      bind(...values) { this.values = values; return this; },
      async run() { return { meta: { changes: 0 } }; },
      async all() { return { results: db.rows }; },
    };
  }
  async batch(statements) {
    return statements.map(({ sql, values }) => {
      const columns = sql.match(/stock_metrics \(([^)]+)\)/)[1].split(',');
      for (let offset = 0; offset < values.length; offset += columns.length) {
        this.rows.push(Object.fromEntries(columns.map((column, i) => [column, values[offset + i]])));
      }
      return { meta: { changes: values.length / columns.length } };
    });
  }
}

async function roundTrip(metric) {
  const db = new DB();
  const env = { DB: db, LOW_CHIP_SYNC_TOKEN: 'test' };
  const headers = { authorization: 'Bearer test', 'content-type': 'application/json' };
  const posted = await onRequest({ env, request: new Request('https://example.test/api/public/v1/low-chip-metrics', {
    method: 'POST', headers, body: JSON.stringify({ metrics: [{
      trade_date: '20261030', stock_code: '600269', ...metric,
    }] }),
  }) });
  assert.equal(posted.status, 200);
  const fetched = await onRequest({ env, request: new Request('https://example.test/api/public/v1/low-chip-metrics?date=20261030', { headers }) });
  assert.equal(fetched.status, 200);
  return { stored: db.rows[0], returned: (await fetched.json()).results[0] };
}

test('POST binding and GET preserve unknown shareholder flags and availability', async () => {
  const nature = {
    quality_shareholder: null, quality_shareholder_names: [],
    institutional_shareholder: null, institutional_shareholder_names: [],
    availability: { industry: 'AVAILABLE', top10_shareholders: 'UNAVAILABLE' },
    status: 'degraded',
  };
  const { stored, returned } = await roundTrip({ quality_shareholder: null, shareholder_nature: nature });
  assert.equal(stored.quality_shareholder, null);
  assert.deepEqual(JSON.parse(stored.shareholder_nature), nature);
  assert.equal(returned.quality_shareholder, null);
  assert.deepEqual(returned.shareholder_nature, nature);
});

for (const quality of [false, true, 0, 1]) {
  test(`POST and GET retain known shareholder scalar ${quality}`, async () => {
    const nature = {
      quality_shareholder: Boolean(quality), institutional_shareholder: false,
      availability: { top10_shareholders: 'AVAILABLE' }, status: 'ok',
    };
    const { stored, returned } = await roundTrip({ quality_shareholder: quality, shareholder_nature: nature });
    assert.equal(stored.quality_shareholder, Number(Boolean(quality)));
    assert.equal(returned.quality_shareholder, Number(Boolean(quality)));
    assert.deepEqual(returned.shareholder_nature, nature);
  });
}

test('omitted legacy shareholder evidence remains null on POST and GET', async () => {
  const { stored, returned } = await roundTrip({});
  assert.equal(stored.quality_shareholder, null);
  assert.equal(returned.quality_shareholder, null);
  assert.equal(returned.shareholder_nature, null);
});
