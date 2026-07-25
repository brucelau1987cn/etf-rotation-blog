import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeQuotePayload } from '../src/lib/normalizeQuotePayload.mjs';

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

  assert.deepEqual(normalizeQuotePayload(payload), {
    ok: true,
    count: 2,
    generated_at: '2026-07-25T09:20:49.350Z',
    items: [
      {
        symbol: 'XLC',
        price: 106.3,
        low: 105.3,
        change_pct: 0.87,
        change_percent: 0.87,
        quote_time: '2026-07-25T09:20:49.350Z',
        status: 'ok',
      },
      {
        symbol: 'QQQ',
        price: 684.23,
        low: 682.48,
        change_pct: -1.12,
        change_percent: -1.12,
        quote_time: '2026-07-25T09:20:49.350Z',
        status: 'ok',
      },
    ],
  });
});

test('preserves the legacy live payload contract', () => {
  const payload = {
    ok: true,
    count: 1,
    generated_at: '2026-07-25T09:20:49.350Z',
    items: [{ symbol: 'XLC', price: 106.3, change_pct: 0.87 }],
  };

  assert.equal(normalizeQuotePayload(payload), payload);
});
