/**
 * 东方财富筹码分布计算器 (CYQ Calculator)
 * 移植自 akshare stock_cyq_em
 * 数据源：腾讯 fqkline 日线（OHLCV）+ 腾讯行情流通股本（计算换手率）
 */

function validateKlines(klines) {
  if (!Array.isArray(klines) || klines.length === 0) throw new Error('empty kline data');
  let previousDate = '';
  for (const kline of klines) {
    const values = [kline.open, kline.close, kline.high, kline.low, kline.volume, kline.hsl];
    if (!kline.date || values.some((value) => !Number.isFinite(value))) throw new Error('invalid kline value');
    if (kline.open <= 0 || kline.close <= 0 || kline.low <= 0 || kline.high < kline.low) throw new Error('invalid OHLC');
    if (kline.open < kline.low || kline.open > kline.high || kline.close < kline.low || kline.close > kline.high) throw new Error('invalid OHLC');
    if (kline.volume < 0 || kline.hsl < 0 || kline.hsl > 100) throw new Error('invalid turnover');
    if (previousDate && kline.date <= previousDate) throw new Error('invalid kline order');
    previousDate = kline.date;
  }
  return klines;
}

/**
 * 从腾讯获取日K线数据（OHLCV + 计算换手率）
 * 先通过 qt.gtimg.cn 获取流通股本，再从 fqkline 获取日线，计算 hsl
 */
async function fetchKlineFromTencent(symbol, adjust = '') {
  const secCode = symbol.startsWith('6') ? `sh${symbol}` : `sz${symbol}`;

  // 1. 获取流通股本（从实时行情）
  const quoteRes = await fetch(`https://qt.gtimg.cn/q=${secCode}`, {
    headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.qq.com/' },
  });
  if (!quoteRes.ok) throw new Error(`tencent quote HTTP ${quoteRes.status}`);
  const buf = await quoteRes.arrayBuffer();
  const dec = new TextDecoder('gbk');
  const quoteText = dec.decode(buf);
  const quoteParts = quoteText.split('~');
  const price = parseFloat(quoteParts[3]) || 0;
  const floatMv = parseFloat(quoteParts[44]) || 0; // 流通市值（亿）
  const floatShares = floatMv > 0 && price > 0 ? Math.round(floatMv * 1e8 / price / 100) : 0; // 流通股本（手）
  if (!Number.isFinite(floatShares) || floatShares <= 0) {
    throw new Error('invalid circulating shares');
  }

  // 2. 获取日K线：不复权走 kline，前/后复权走 fqkline。
  const mode = adjust === '' ? 'day' : `${adjust}day`;
  const url = adjust === ''
    ? `https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=${secCode},day,,,320`
    : `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${secCode},day,,,320,${adjust}`;
  const res = await fetch(url, {
    headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.qq.com/' },
  });
  if (!res.ok) throw new Error(`tencent kline HTTP ${res.status}`);
  const data = await res.json();
  const kdata = data?.data?.[secCode]?.[mode];
  if (!kdata || !kdata.length) throw new Error('tencent kline empty');

  // 腾讯日K: [date, open, close, high, low, volume]
  return validateKlines(kdata.map((k) => {
    const vol = Number(k[5]); // 成交量（手）
    return {
      date: String(k[0] || ''),
      open: Number(k[1]),
      close: Number(k[2]),
      high: Number(k[3]),
      low: Number(k[4]),
      volume: vol,
      hsl: Math.min(100, (vol / floatShares) * 100), // 换手率(%)
    };
  }));
}

/**
 * 从 push2his 获取日K线数据（可能被限制，作为 fallback）
 */
async function fetchKlineFromPush2his(secid, adjust) {
  const adjustMap = { 'qfq': '1', 'hfq': '2', '': '0' };
  const url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get';
  const params = new URLSearchParams({
    secid,
    fields1: 'f1,f2,f3,f4,f5,f6',
    fields2: 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
    klt: '101',
    fqt: adjustMap[adjust] || '0',
    lmt: '210',
    end: new Date().toISOString().slice(0, 10).replace(/-/g, ''),
  });
  const res = await fetch(`${url}?${params}`, {
    headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/' },
  });
  if (!res.ok) throw new Error(`push2his HTTP ${res.status}`);
  const d = await res.json();
  if (d.rc !== 0 || !d.data?.klines) throw new Error(`push2his rc=${d.rc}`);
  return validateKlines(d.data.klines.map((line) => {
    const parts = line.split(',');
    return {
      date: parts[0], open: Number(parts[1]), close: Number(parts[2]),
      high: Number(parts[3]), low: Number(parts[4]),
      volume: Number(parts[5]), hsl: Number(parts[10]),
    };
  }));
}

/**
 * 计算筹码分布（CYQ 算法）
 * 输入: 日K线数组 (open, close, high, low, hsl)
 * 输出: 最新日的获利比例、平均成本、90/70集中度、90/70成本区间
 */
function computeChipDistributionAt(klines, index) {
  const factor = 150;
  const range = 120;
  if (klines.length === 0 || index < 0 || index >= klines.length) return null;

  const start = Math.max(0, index - range + 1);
  const kdata = klines.slice(start, Math.max(1, index + 1));

  let maxprice = 0, minprice = 0;
  for (const k of kdata) {
    maxprice = !maxprice ? k.high : Math.max(maxprice, k.high);
    minprice = !minprice ? k.low : Math.min(minprice, k.low);
  }

  const accuracy = Math.max(0.01, (maxprice - minprice) / (factor - 1));
  const yrange = Array.from({ length: factor }, (_, i) => +(minprice + accuracy * i).toFixed(2));
  const xdata = new Array(factor).fill(0);

  for (const k of kdata) {
    const avg = (k.open + k.close + k.high + k.low) / 4;
    const turnoverRate = Math.min(1, (k.hsl || 0) / 100);
    const H = Math.floor((k.high - minprice) / accuracy);
    const L = Math.ceil((k.low - minprice) / accuracy);
    const GPoint = k.high === k.low
      ? [factor - 1, Math.floor((avg - minprice) / accuracy)]
      : [2 / (k.high - k.low), Math.floor((avg - minprice) / accuracy)];

    for (let n = 0; n < factor; n++) xdata[n] *= (1 - turnoverRate);

    if (k.high === k.low) {
      xdata[GPoint[1]] += GPoint[0] * turnoverRate / 2;
    } else {
      for (let j = L; j <= H; j++) {
        const curprice = minprice + accuracy * j;
        if (curprice <= avg) {
          xdata[j] += Math.abs(avg - k.low) < 1e-8
            ? GPoint[0] * turnoverRate
            : (curprice - k.low) / (avg - k.low) * GPoint[0] * turnoverRate;
        } else {
          xdata[j] += Math.abs(k.high - avg) < 1e-8
            ? GPoint[0] * turnoverRate
            : (k.high - curprice) / (k.high - avg) * GPoint[0] * turnoverRate;
        }
      }
    }
  }

  const currentprice = kdata[kdata.length - 1].close;
  let totalChips = 0;
  for (let i = 0; i < factor; i++) totalChips += xdata[i];

  function getCostByChip(chip) {
    if (totalChips === 0) return 0;
    let sum = 0;
    for (let i = 0; i < factor; i++) {
      sum += xdata[i];
      if (sum > chip) return minprice + i * accuracy;
    }
    return 0;
  }

  let below = 0;
  for (let i = 0; i < factor; i++) {
    if (currentprice >= minprice + i * accuracy) below += xdata[i];
  }
  const benefitPart = totalChips === 0 ? 0 : below / totalChips;
  const avgCost = getCostByChip(totalChips * 0.5);

  function computePercentChips(percent) {
    const ps = [(1 - percent) / 2, (1 + percent) / 2];
    const pr = [getCostByChip(totalChips * ps[0]), getCostByChip(totalChips * ps[1])];
    return {
      priceRange: [+pr[0].toFixed(2), +pr[1].toFixed(2)],
      concentration: pr[0] + pr[1] === 0 ? 0 : (pr[1] - pr[0]) / (pr[0] + pr[1]),
    };
  }

  const pct90 = computePercentChips(0.9);
  const pct70 = computePercentChips(0.7);

  return {
    benefitPart: +(benefitPart * 100).toFixed(2), // 转为百分比
    avgCost: +avgCost.toFixed(2),
    avgCostPct: +((avgCost - currentprice) / currentprice * 100).toFixed(2), // 平均成本相对现价偏离
    pct90: {
      low: pct90.priceRange[0],
      high: pct90.priceRange[1],
      concentration: +(pct90.concentration * 100).toFixed(2),
    },
    pct70: {
      low: pct70.priceRange[0],
      high: pct70.priceRange[1],
      concentration: +(pct70.concentration * 100).toFixed(2),
    },
  };
}

function computeChipDistribution(klines) {
  return computeChipDistributionAt(klines, klines.length - 1);
}

function computeChipDistributionSeries(klines, limit = 90) {
  const count = Math.max(1, Math.min(90, Number(limit) || 90));
  const all = klines.map((kline, index) => {
    const chip = computeChipDistributionAt(klines, index);
    return {
      date: kline.date,
      profit_ratio_pct: chip.benefitPart,
      average_cost: chip.avgCost,
      average_cost_deviation_pct: chip.avgCostPct,
      cost_90_low: chip.pct90.low,
      cost_90_high: chip.pct90.high,
      concentration_90_pct: chip.pct90.concentration,
      cost_70_low: chip.pct70.low,
      cost_70_high: chip.pct70.high,
      concentration_70_pct: chip.pct70.concentration,
    };
  });
  return all.slice(-count);
}

export {
  fetchKlineFromTencent,
  fetchKlineFromPush2his,
  computeChipDistribution,
  computeChipDistributionSeries,
};