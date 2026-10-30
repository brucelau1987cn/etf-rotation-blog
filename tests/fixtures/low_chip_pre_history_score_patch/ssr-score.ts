const chipFocusScore = (focus: string | null) => (focus === '非常集中' ? 4 : focus === '较集中' ? 2 : 0);
const compositeScore = (symbol: string, enr: any) => {
  const profits = profitBySymbol[symbol];
  const sm = enr.shareholder_metrics || {};
  const fin = enr.financials || {};
  const pValues = [profits?.week, profits?.month, profits?.quarter].filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
  // 筹码干净度 35: 三周期均值越低越好; 全部≈0(浮筹清零) 封顶
  let profitScore: number;
  if (!pValues.length) profitScore = 15;
  else {
    const pa = pValues.reduce((a, b) => a + b, 0) / pValues.length;
    const allZero = pValues.every((v) => v <= 0.05);
    profitScore = allZero ? 35 : pa <= 0.1 ? 32 : pa <= 0.5 ? 28 : pa <= 1.0 ? 20 : pa <= 1.3 ? 14 : 10;
  }
  // 主力吸筹 25
  const mf = Number(sm.main_force);
  const mfScore = Number.isFinite(mf) ? (mf >= 35 ? 25 : mf >= 28 ? 20 : mf >= 22 ? 14 : 8) : 8;
  // 股东收敛 + 筹码集中 20 (股东变化为负=户数减少=筹码集中)
  const shChg = Number(sm.shareholder_change_pct);
  let shScore = 0;
  if (Number.isFinite(shChg)) shScore = shChg <= -12 ? 16 : shChg <= -5 ? 14 : shChg < 0 ? 11 : shChg <= 6 ? 6 : 2;
  else shScore = 6;
  shScore = Math.min(20, shScore + chipFocusScore(sm.chip_focus || null));
  // 财务质地 20 (下限保护: 盈利/低负债加分, 亏损不全否决)
  const roe = Number(fin.roe);
  const roeScore = Number.isFinite(roe) ? (roe >= 8 ? 8 : roe >= 4 ? 6 : roe >= 0 ? 4 : 0) : 2;
  const net = Number(fin.net_margin);
  const netScore = Number.isFinite(net) ? (net >= 15 ? 6 : net >= 5 ? 4 : net >= 0 ? 2 : 0) : 2;
  const debt = Number(fin.debt_ratio);
  const debtScore = Number.isFinite(debt) ? (debt < 30 ? 6 : debt < 50 ? 4 : debt < 70 ? 2 : 0) : 2;
  const finScore = Math.min(20, roeScore + netScore + debtScore);
  return Math.round(profitScore + mfScore + shScore + finScore);
};
