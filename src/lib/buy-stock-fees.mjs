/**
 * Build-time re-export of the canonical A-share fee model.
 *
 * The canonical copy lives in public/js/buy-stock-app.js, which the browser
 * loads as an ES module. This module lets Astro build-time code and the
 * node test suite import the same implementation.
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
} from '../../public/js/buy-stock-app.js';
