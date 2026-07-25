import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeQuotePayload } from '../src/lib/normalizeQuotePayload.mjs';

test('extracts single quote directly for stock-quote UI', () => {
  const payload = {
    status: 'ok',
    count: 1,
    quotes: {
      '600021': {
        symbol: '600021',
        sec_code: 'sh600021',
        price: 14.21,
        change_percent: -7.37,
        status: 'ok',
      },
    },
  };

  const normalized = normalizeQuotePayload(payload);
  const quote = normalized.items.find((item) => item.symbol === '600021') || normalized.items[0];

  assert.ok(quote);
  assert.equal(quote.price, 14.21);
  assert.equal(quote.change_percent, -7.37);
});
