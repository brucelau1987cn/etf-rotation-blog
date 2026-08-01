import assert from 'node:assert/strict';
import test from 'node:test';

import { findQuoteItem, normalizeQuotePayload } from '../src/lib/normalizeQuotePayload.mjs';

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
  const quote = findQuoteItem(normalized, '600021');

  assert.ok(quote);
  assert.equal(quote.symbol, '600021');
  assert.equal(quote.code, '600021');
  assert.equal(quote.price, 14.21);
  assert.equal(quote.change_percent, -7.37);
});

test('preserves change_amount for index/quote UI', () => {
  const payload = {
    status: 'ok',
    count: 1,
    quotes: {
      '000001': {
        symbol: '000001',
        sec_code: 'sh000001',
        price: 3832.26,
        change_amount: 27.57,
        change_percent: 0.72,
        status: 'ok',
      },
    },
  };

  const normalized = normalizeQuotePayload(payload);
  const quote = findQuoteItem(normalized, '000001');

  assert.ok(quote);
  assert.equal(quote.change_amount, 27.57);
  assert.equal(quote.change_percent, 0.72);
});

test('matches Tencent US suffix symbols to bare rolling tickers', () => {
  const payload = {
    status: 'ok',
    count: 4,
    quotes: {
      '.INX': { symbol: '.INX', sec_code: 'usINX', price: 7411.98, change_percent: 0.05 },
      '.IXIC': { symbol: '.IXIC', sec_code: 'usIXIC', price: 24975.82, change_percent: -0.64 },
      '.DJI': { symbol: '.DJI', sec_code: 'usDJI', price: 51947.25, change_percent: 0.46 },
      'TSLA.OQ': { symbol: 'TSLA.OQ', sec_code: 'usTSLA', price: 313.03, change_percent: -2.08 },
    },
  };

  const normalized = normalizeQuotePayload(payload);
  assert.equal(findQuoteItem(normalized, 'INX')?.price, 7411.98);
  assert.equal(findQuoteItem(normalized, 'IXIC')?.price, 24975.82);
  assert.equal(findQuoteItem(normalized, 'DJI')?.price, 51947.25);
  assert.equal(findQuoteItem(normalized, 'TSLA')?.price, 313.03);
});
