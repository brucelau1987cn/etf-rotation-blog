/**
 * Canonical A-share fee model. See public/js/buy-stock-fees.js for the rules.
 *
 * This module re-exports the browser copy so build-time code (Astro pages)
 * and the node test suite share one implementation. Keep the two files in
 * sync — tests/buy_stock_fees.test.mjs asserts they match.
 */
export {
  DEFAULT_FEES,
  FEE_LABELS,
  round2,
  ceil2,
  computeBuyFees,
  computeSellFees,
  computeRoundTripFees,
  breakEvenPrice,
} from '../../public/js/buy-stock-fees.js';
