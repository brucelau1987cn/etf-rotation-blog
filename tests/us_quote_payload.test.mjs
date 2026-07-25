import assert from 'node:assert/strict';
import test from 'node:test';

import {
  aShareSymbolsParam,
  findQuoteItem,
  normalizeQuotePayload,
} from '../src/lib/normalizeQuotePayload.mjs';

test('normalizes edge quote API payload for US compass cards', () => {
  const payload = {
    status: 'ok',
    count: 2,
    quotes: {
      'XLC.AM': {
        symbol: 'XLC.AM',
        sec_code: 'usXLC',
        price: 106.3,
        low: 105.3,
        change_percent: 0.87,
        quote_time: '2026-07-25T09:20:49.350Z',
      },
      'QQQ.OQ': {
        symbol: 'QQQ.OQ',
        sec_code: 'usQQQ',
        price: 684.23,
        low: 682.48,
        change_percent: -1.12,
        quote_time: '2026-07-25T09:20:49.350Z',
      },
    },
  };

  const normalized = normalizeQuotePayload(payload);
  assert.equal(normalized.ok, true);
  assert.equal(normalized.count, 2);
  assert.equal(normalized.generated_at, '2026-07-25T09:20:49.350Z');
  assert.deepEqual(
    normalized.items.map((item) => ({
      symbol: item.symbol,
      code: item.code,
      price: item.price,
      low: item.low,
      change_pct: item.change_pct,
      change_percent: item.change_percent,
      quote_time: item.quote_time,
      status: item.status,
    })),
    [
      {
        symbol: 'XLC',
        code: 'XLC',
        price: 106.3,
        low: 105.3,
        change_pct: 0.87,
        change_percent: 0.87,
        quote_time: '2026-07-25T09:20:49.350Z',
        status: 'ok',
      },
      {
        symbol: 'QQQ',
        code: 'QQQ',
        price: 684.23,
        low: 682.48,
        change_pct: -1.12,
        change_percent: -1.12,
        quote_time: '2026-07-25T09:20:49.350Z',
        status: 'ok',
      },
    ],
  );
});

test('preserves the legacy live payload contract fields', () => {
  const payload = {
    ok: true,
    count: 1,
    generated_at: '2026-07-25T09:20:49.350Z',
    items: [{ symbol: 'XLC', price: 106.3, change_pct: 0.87 }],
  };

  const normalized = normalizeQuotePayload(payload);
  assert.equal(normalized.ok, true);
  assert.equal(normalized.items[0].symbol, 'XLC');
  assert.equal(normalized.items[0].code, 'XLC');
  assert.equal(normalized.items[0].price, 106.3);
  assert.equal(normalized.items[0].change_pct, 0.87);
  assert.equal(normalized.items[0].change_percent, 0.87);
});

test('builds A-share symbols param and finds codes after suffix strip', () => {
  assert.equal(aShareSymbolsParam(['600021', '159915', '300750']), '600021.SH,159915.SZ,300750.SZ');
  const normalized = normalizeQuotePayload({
    status: 'ok',
    quotes: {
      '600021.SH': { symbol: '600021.SH', sec_code: 'sh600021', price: 14.21, change_percent: -7.37 },
    },
  });
  assert.equal(findQuoteItem(normalized, '600021')?.price, 14.21);
  assert.equal(findQuoteItem(normalized, '600021.SH')?.price, 14.21);
});
