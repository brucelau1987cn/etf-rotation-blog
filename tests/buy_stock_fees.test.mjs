import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  DEFAULT_FEES,
  computeBuyFees,
  computeSellFees,
  computeRoundTripFees,
  breakEvenPrice,
  round2,
} from '../public/js/buy-stock-fees.js';

const canonical = await readFile(new URL('../public/js/buy-stock-fees.js', import.meta.url), 'utf8');
const buildCopy = await readFile(new URL('../src/lib/buy-stock-fees.mjs', import.meta.url), 'utf8');

test('the build-time re-export points at the canonical browser copy', () => {
  assert.match(buildCopy, /public\/js\/buy-stock-fees\.js/);
  for (const name of [
    'DEFAULT_FEES',
    'FEE_LABELS',
    'round2',
    'computeBuyFees',
    'computeSellFees',
    'computeRoundTripFees',
    'breakEvenPrice',
  ]) {
    assert.ok(canonical.includes(name), `canonical copy must export ${name}`);
    assert.ok(buildCopy.includes(name), `build copy must re-export ${name}`);
  }
});

test('fee rates follow the current A-share rules', () => {
  assert.equal(DEFAULT_FEES.commission_rate, 0.00025);
  assert.equal(DEFAULT_FEES.commission_min, 5);
  assert.equal(DEFAULT_FEES.stamp_duty_rate, 0.0005);
  assert.equal(DEFAULT_FEES.transfer_fee_rate, 0.00001);
});

test('a buy charges commission and transfer fee but no stamp duty', () => {
  // 5000 shares at 7.45 => 37250 turnover.
  const fees = computeBuyFees(37250);
  assert.equal(fees.commission, 9.31);
  assert.equal(fees.stamp_duty, 0);
  assert.equal(fees.transfer_fee, 0.37);
  // The total must equal the sum of the displayed components.
  assert.equal(fees.total, 9.68);
});

test('the ¥5 commission floor applies on small orders', () => {
  const fees = computeBuyFees(1000);
  assert.equal(fees.commission, 5);
  assert.equal(fees.total, 5.01);
});

test('a sell charges stamp duty on top of commission and transfer fee', () => {
  const fees = computeSellFees(100000);
  assert.equal(fees.commission, 25);
  assert.equal(fees.stamp_duty, 50);
  assert.equal(fees.transfer_fee, 1);
  assert.equal(fees.total, 76);
});

test('round-trip fees sum both sides', () => {
  const fees = computeRoundTripFees(37250, 37250);
  assert.equal(fees.buy, 9.68);
  assert.equal(fees.sell, 28.31);
  assert.equal(fees.total, 37.99);
});

test('break-even price sits above the entry price', () => {
  const position = { buy_price: 7.45, shares: 5000 };
  const p = breakEvenPrice(position);
  assert.ok(p > 7.45, `break-even ${p} must exceed the entry price`);
});

test('break-even price covers the fees: P&L is non-negative there and negative one cent below', () => {
  const position = { buy_price: 7.45, shares: 5000 };
  const p = breakEvenPrice(position);
  const netAt = (price) => {
    const sellFees = computeSellFees(price * position.shares);
    const buyFees = computeBuyFees(position.buy_price * position.shares);
    return round2(
      price * position.shares - sellFees.total
      - (position.buy_price * position.shares + buyFees.total),
    );
  };
  // The returned price must not leave the position underwater.
  assert.ok(netAt(p) >= 0, `net P&L at break-even must be >= 0, got ${netAt(p)}`);
  // One cent below must still be underwater, so the figure is tight.
  assert.ok(netAt(p - 0.01) < 0, `net P&L one cent below must be < 0, got ${netAt(p - 0.01)}`);
});

test('break-even price covers the ¥5 commission floor on a small position', () => {
  // 1000 shares at 2.00 => 2000 turnover, below the proportional commission.
  const position = { buy_price: 2, shares: 1000 };
  const p = breakEvenPrice(position);
  const sellFees = computeSellFees(p * position.shares);
  const buyFees = computeBuyFees(position.buy_price * position.shares);
  const net = round2(
    p * position.shares - sellFees.total - (position.buy_price * position.shares + buyFees.total),
  );
  assert.ok(net >= 0, `net P&L at break-even must be >= 0, got ${net}`);
  assert.ok(p > 2, `break-even ${p} must exceed the entry price`);
});

test('break-even price handles a zero-cost position without dividing by zero', () => {
  assert.equal(breakEvenPrice({ buy_price: 0, shares: 1000 }), 0);
  assert.equal(breakEvenPrice({ buy_price: 7.45, shares: 0 }), 0);
});
