/**
 * A-share trading cost model for the buy-stock holdings page.
 *
 * Canonical copy. `src/lib/buy-stock-fees.mjs` re-exports this module so the
 * Astro page and the node tests share one implementation, and the browser
 * bundle imports it directly as `./buy-stock-fees.js`.
 *
 * Fee rules (A-share ordinary accounts, 2026):
 *  - 券商佣金: 0.025% of turnover, charged on BOTH buy and sell, minimum ¥5 per order.
 *  - 印花税: 0.05% of turnover, charged on SELL only (halved since 2023-08-28).
 *  - 过户费: 0.001% of turnover, charged on BOTH buy and sell, both exchanges
 *    (unified to 0.01‰ bidirectional since 2022-04-29).
 */

export const DEFAULT_FEES = Object.freeze({
  commission_rate: 0.00025,
  commission_min: 5,
  stamp_duty_rate: 0.0005,
  transfer_fee_rate: 0.00001,
});

export const FEE_LABELS = Object.freeze({
  commission: '佣金',
  stamp_duty: '印花税',
  transfer_fee: '过户费',
});

export function round2(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

/**
 * Round UP to 2 decimals. Used for break-even price so the displayed figure
 * is never below the true break-even — rounding to nearest could show a
 * price that still loses money after fees.
 */
export function ceil2(value) {
  return Math.ceil((Number(value) - Number.EPSILON) * 100) / 100;
}

/**
 * Buy-side costs for one order. Stamp duty does not apply to buys.
 */
export function computeBuyFees(turnover, fees = DEFAULT_FEES) {
  const amount = Number(turnover) || 0;
  const commission = round2(Math.max(amount * fees.commission_rate, fees.commission_min));
  const transferFee = round2(amount * fees.transfer_fee_rate);
  // Total is the sum of the ROUNDED components so the breakdown always adds up.
  return {
    commission,
    stamp_duty: 0,
    transfer_fee: transferFee,
    total: round2(commission + transferFee),
  };
}

/**
 * Sell-side costs for one order, assuming the full position is sold.
 * Stamp duty applies to sells only.
 */
export function computeSellFees(turnover, fees = DEFAULT_FEES) {
  const amount = Number(turnover) || 0;
  const commission = round2(Math.max(amount * fees.commission_rate, fees.commission_min));
  const stampDuty = round2(amount * fees.stamp_duty_rate);
  const transferFee = round2(amount * fees.transfer_fee_rate);
  return {
    commission,
    stamp_duty: stampDuty,
    transfer_fee: transferFee,
    total: round2(commission + stampDuty + transferFee),
  };
}

/**
 * Round-trip cost model for a position, used for the "breakeven" figure.
 */
export function computeRoundTripFees(buyTurnover, sellTurnover, fees = DEFAULT_FEES) {
  const buy = computeBuyFees(buyTurnover, fees);
  const sell = computeSellFees(sellTurnover, fees);
  return {
    buy: buy.total,
    sell: sell.total,
    total: round2(buy.total + sell.total),
  };
}

/**
 * Break-even sell price: the price at which the position nets zero P&L
 * after both sides' fees.
 */
export function breakEvenPrice(position, fees = DEFAULT_FEES) {
  const shares = Number(position.shares) || 0;
  const buyPrice = Number(position.buy_price) || 0;
  if (shares <= 0 || buyPrice <= 0) return 0;
  const buyTurnover = buyPrice * shares;
  const buyFees = computeBuyFees(buyTurnover, fees);
  const cost = buyTurnover + buyFees.total;

  // Solve p*shares - sellFees(p*shares) = cost for p. The sell-side commission
  // is max(turnover * rate, floor), so it has two regimes and each yields an
  // analytic solution:
  //   proportional: p*shares*(1 - rate - stamp - transfer) = cost
  //   floor:        p*shares*(1 - stamp - transfer) = cost + floor
  // Each candidate is kept only when its own regime assumption holds.
  const proportionalRate = fees.commission_rate + fees.stamp_duty_rate + fees.transfer_fee_rate;
  const floorRate = fees.stamp_duty_rate + fees.transfer_fee_rate;
  const candidates = [];

  const pProportional = cost / (shares * (1 - proportionalRate));
  if (pProportional * shares * fees.commission_rate >= fees.commission_min) {
    candidates.push(pProportional);
  }

  const pFloor = (cost + fees.commission_min) / (shares * (1 - floorRate));
  if (pFloor * shares * fees.commission_rate < fees.commission_min) {
    candidates.push(pFloor);
  }

  // At least one regime must hold; taking the max keeps the figure
  // conservative (never understates what the fees require).
  const raw = candidates.length > 0 ? Math.max(...candidates) : Math.max(pProportional, pFloor);
  // Round up: the displayed price must actually cover the fees.
  return ceil2(raw);
}
