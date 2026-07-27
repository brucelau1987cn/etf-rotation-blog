(function (root) {
  const structuralStatuses = new Set(['defense', 'cash']);

  function adjustedReturn(base, oldPrice, price) {
    const value = Number(base);
    if (!Number.isFinite(value) || !Number.isFinite(oldPrice) || oldPrice <= 0) return value;
    return (((1 + value / 100) * price / oldPrice) - 1) * 100;
  }

  function recomputeMomentumRow(row, livePrice) {
    const price = Number(livePrice);
    const oldPrice = Number(row.price);
    const ma20Snapshot = Number(row.ma20);
    const ma20Prev = Number(row.ma20_prev);
    const slope20 = Number(row.slope20);
    const ret3 = adjustedReturn(row.ret3, oldPrice, price);
    const ret5 = adjustedReturn(row.ret5, oldPrice, price);
    const ret10 = adjustedReturn(row.ret10, oldPrice, price);
    const ret20 = adjustedReturn(row.ret20, oldPrice, price);
    const ma20 = Number.isFinite(ma20Snapshot) && Number.isFinite(oldPrice)
      ? ma20Snapshot + (price - oldPrice) / 20
      : ma20Snapshot;
    const canRecompute = [price, oldPrice, ma20, ma20Prev, slope20, ret3, ret5]
      .every(Number.isFinite) && oldPrice > 0;
    if (!canRecompute) return { ...row, price };

    const priceAboveMa = price > ma20;
    const maRising = ma20 > ma20Prev;
    const shortOk = ret3 > -5;
    const dualMomentum = ret5 > 0 && slope20 > 0 && priceAboveMa;
    const momentum = priceAboveMa && maRising && shortOk && dualMomentum;
    const status = structuralStatuses.has(row.status) ? row.status : (momentum ? 'core' : 'watch');
    const rounded = value => Number.isFinite(value) ? Number(value.toFixed(2)) : value;
    return {
      ...row,
      price,
      ret3: rounded(ret3),
      ret5: rounded(ret5),
      ret10: rounded(ret10),
      ret20: rounded(ret20),
      ma20: Number.isFinite(ma20) ? Number(ma20.toFixed(4)) : row.ma20,
      status,
      checks: {
        ...(row.checks || {}),
        price_above_ma: priceAboveMa,
        ma_rising: maRising,
        short_ok: shortOk,
        dual_momentum: dualMomentum,
        momentum,
      },
    };
  }

  root.AMomentumRecompute = { recomputeMomentumRow };
})(typeof window !== 'undefined' ? window : globalThis);
