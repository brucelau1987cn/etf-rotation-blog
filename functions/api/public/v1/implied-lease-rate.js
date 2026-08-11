/**
 * GET /api/public/v1/implied-lease-rate
 *
 * Market-implied precious-metal lease proxy on Cloudflare Pages Functions:
 *   lease(T) ≈ r_USD(T) − (1/T) * ln(F/S)
 *
 * Sources:
 *   - COMEX futures via Yahoo Finance chart API (GC*.CMX / SI*.CMX)
 *   - US Treasury nominal yield curve CSV
 *
 * Cached 10 minutes at the edge. This is NOT an official LBMA/Kitco lease quote.
 */

const CACHE_TTL_MS = 10 * 60 * 1000;
const CACHE_KEY = 'https://etf.peekabo.cc/__cache/implied-lease-rate/v1';
const GOLD_MONTH_CODES = 'GJMQVZ';
const SILVER_MONTH_CODES = 'HKNUZ';
const TENORS = [
  { label: '1M', days: 30, usdKey: '1M' },
  { label: '3M', days: 91, usdKey: '3M' },
  { label: '6M', days: 182, usdKey: '6M' },
  { label: '1Y', days: 365, usdKey: '1Y' },
];
const MONTH_NAME_TO_NUM = {
  Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6,
  Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12,
};

const json = (payload, status = 200, cacheControl = 'public, max-age=600, s-maxage=600') =>
  new Response(JSON.stringify(payload), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': cacheControl,
      'access-control-allow-origin': '*',
    },
  });

function cnToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const map = Object.fromEntries(parts.filter((p) => p.type !== 'literal').map((p) => [p.type, p.value]));
  return new Date(Date.UTC(Number(map.year), Number(map.month) - 1, Number(map.day)));
}

function daysBetween(a, b) {
  return Math.round((b.getTime() - a.getTime()) / 86400000);
}

function parseExpiry(shortName) {
  if (!shortName) return null;
  const m = String(shortName).match(/(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})/);
  if (!m) return null;
  const mon = MONTH_NAME_TO_NUM[m[1]];
  const yr = 2000 + Number(m[2]);
  const day = 27;
  return new Date(Date.UTC(yr, mon - 1, day));
}

function leaseProxy(spot, forward, years, usdRatePct) {
  if (!(spot > 0) || !(forward > 0) || !(years > 0) || usdRatePct == null) return null;
  const forwardYield = (Math.log(forward / spot) / years) * 100;
  return usdRatePct - forwardYield;
}

function interpolateUsd(usd, years) {
  const points = [
    [30 / 365.25, usd['1M']],
    [91 / 365.25, usd['3M']],
    [182 / 365.25, usd['6M']],
    [365 / 365.25, usd['1Y']],
  ];
  if (years <= points[0][0]) return points[0][1];
  if (years >= points[points.length - 1][0]) return points[points.length - 1][1];
  for (let i = 1; i < points.length; i++) {
    const [t0, r0] = points[i - 1];
    const [t1, r1] = points[i];
    if (years >= t0 && years <= t1) {
      const w = (years - t0) / (t1 - t0);
      return r0 + w * (r1 - r0);
    }
  }
  return points[points.length - 1][1];
}

function filterLiquid(rows, today) {
  const cleaned = rows
    .filter((r) => r.price > 0 && r.expiry && daysBetween(today, r.expiry) > 3)
    .sort((a, b) => a.expiry - b.expiry);
  if (!cleaned.length) return [];
  const good = [cleaned[0]];
  for (let i = 1; i < cleaned.length; i++) {
    const r = cleaned[i];
    const prev = good[good.length - 1].price;
    if (r.price < prev * 0.97) continue;
    if (r.price > prev * 1.09) continue;
    if (daysBetween(today, r.expiry) > 430 && r.price > good[0].price * 1.2) continue;
    good.push(r);
  }
  return good;
}

function pickContract(rows, targetDays, today) {
  if (!rows.length) return null;
  const candidates = rows.length > 1 ? rows.slice(1) : rows;
  let best = null;
  let bestDiff = Infinity;
  for (const r of candidates) {
    const days = daysBetween(today, r.expiry);
    if (days < 10) continue;
    const diff = Math.abs(days - targetDays);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = r;
    }
  }
  return best;
}

function buildMetal(rows, usd, today) {
  const liquid = filterLiquid(rows, today);
  if (!liquid.length) return null;
  const spot = liquid[0];
  const tenors = [];
  for (const t of TENORS) {
    const fwd = pickContract(liquid, t.days, today);
    if (!fwd) continue;
    const years = daysBetween(today, fwd.expiry) / 365.25;
    const rUsd = interpolateUsd(usd, years);
    const rate = leaseProxy(spot.price, fwd.price, years, rUsd);
    if (rate == null) continue;
    tenors.push({
      tenor: t.label,
      target_days: t.days,
      days_to_expiry: daysBetween(today, fwd.expiry),
      years: Number(years.toFixed(4)),
      rate: Number(rate.toFixed(3)),
      usd_rate: Number(rUsd.toFixed(3)),
      usd_bucket: t.usdKey,
      usd_bucket_rate: usd[t.usdKey],
      spot_symbol: spot.symbol,
      spot_name: spot.name,
      spot_price: spot.price,
      forward_symbol: fwd.symbol,
      forward_name: fwd.name,
      forward_price: fwd.price,
      forward_expiry: fwd.expiry.toISOString().slice(0, 10),
      fwd_over_spot: Number((fwd.price / spot.price).toFixed(6)),
    });
  }
  if (!tenors.length) return null;
  const by = Object.fromEntries(tenors.map((x) => [x.tenor, x.rate]));
  return {
    front: {
      symbol: spot.symbol,
      name: spot.name,
      price: spot.price,
      expiry: spot.expiry.toISOString().slice(0, 10),
    },
    tenors,
    rate_1m: by['1M'] ?? null,
    rate_3m: by['3M'] ?? null,
    rate_6m: by['6M'] ?? null,
    rate_1y: by['1Y'] ?? null,
    contracts_used: liquid.length,
  };
}

async function fetchText(url) {
  const resp = await fetch(url, {
    headers: {
      'user-agent': 'Mozilla/5.0 (compatible; etf-compass-implied-lease/1.0)',
      accept: '*/*',
    },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${url}`);
  return resp.text();
}

async function fetchYahooContract(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1d&range=5d`;
  const raw = JSON.parse(await fetchText(url));
  const result = raw?.chart?.result?.[0];
  if (!result) return null;
  const meta = result.meta || {};
  const price = meta.regularMarketPrice;
  const name = meta.shortName || meta.longName || '';
  const expiry = parseExpiry(name);
  if (!(price > 0) || !expiry) return null;
  return {
    symbol,
    price: Number(price),
    name,
    expiry,
    exchange: meta.fullExchangeName || 'COMEX',
  };
}

async function fetchComexCurve(metal) {
  const codes = metal === 'gold' ? GOLD_MONTH_CODES : SILVER_MONTH_CODES;
  const prefix = metal === 'gold' ? 'GC' : 'SI';
  const years = [26, 27, 28];
  const symbols = [];
  for (const y of years) {
    for (const c of codes) symbols.push(`${prefix}${c}${y}.CMX`);
  }
  // Parallel with modest concurrency via Promise.allSettled
  const settled = await Promise.allSettled(symbols.map((s) => fetchYahooContract(s)));
  const rows = [];
  for (const s of settled) {
    if (s.status === 'fulfilled' && s.value) rows.push(s.value);
  }
  return rows;
}

async function fetchTreasuryCurve() {
  const year = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai', year: 'numeric' }).format(new Date());
  const url = `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/${year}/all?type=daily_treasury_yield_curve&field_tdr_date_value=${year}&_format=csv`;
  const csv = await fetchText(url);
  const lines = csv.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) throw new Error('empty treasury csv');
  const headers = lines[0].split(',').map((h) => h.trim().replace(/^"|"$/g, ''));
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',').map((p) => p.trim().replace(/^"|"$/g, ''));
    const row = Object.fromEntries(headers.map((h, idx) => [h, parts[idx]]));
    const oneM = Number(row['1 Mo']);
    const threeM = Number(row['3 Mo']);
    const sixM = Number(row['6 Mo']);
    const oneY = Number(row['1 Yr']);
    if ([oneM, threeM, sixM, oneY].every((n) => Number.isFinite(n))) {
      return {
        date: row.Date || row.date,
        source: 'us_treasury_yield_curve',
        '1M': oneM,
        '3M': threeM,
        '6M': sixM,
        '1Y': oneY,
      };
    }
  }
  throw new Error('no valid treasury row');
}

async function computeLive() {
  const today = cnToday();
  const [usd, goldRows, silverRows] = await Promise.all([
    fetchTreasuryCurve(),
    fetchComexCurve('gold'),
    fetchComexCurve('silver'),
  ]);
  const usdRates = { '1M': usd['1M'], '3M': usd['3M'], '6M': usd['6M'], '1Y': usd['1Y'] };
  const gold = buildMetal(goldRows, usdRates, today);
  const silver = buildMetal(silverRows, usdRates, today);
  const ok = Boolean(gold || silver);
  let headline = null;
  let headlineMetal = null;
  if (gold?.rate_1m != null) {
    headline = gold.rate_1m;
    headlineMetal = 'gold';
  } else if (silver?.rate_1m != null) {
    headline = silver.rate_1m;
    headlineMetal = 'silver';
  }
  return {
    ok,
    source: 'implied_lease',
    method: 'comex_forward_proxy',
    label: '隐含租赁利率（期货曲线估算）',
    formula: 'lease ≈ r_USD(T) − (1/T)*ln(F/S)',
    as_of: today.toISOString().slice(0, 10),
    fetched_at: new Date().toISOString(),
    usd_curve: {
      date: usd.date,
      source: usd.source,
      rates: usdRates,
    },
    gold,
    silver,
    headline_rate: headline,
    headline_metal: headlineMetal,
    note: '基于 COMEX 期货曲线与美国国债收益率估算的隐含租赁/持有成本 proxy；不是 LBMA/Kitco 官方 lease 报价。含展期、保证金与便利收益噪声，白银波动更大。',
    error: ok ? null : 'no liquid COMEX curve',
  };
}

export async function onRequestGet({ request }) {
  try {
    const cache = caches.default;
    const cached = await cache.match(CACHE_KEY);
    if (cached) {
      const clone = new Response(cached.body, cached);
      clone.headers.set('x-cache', 'HIT');
      return clone;
    }

    const data = await computeLive();
    const body = {
      status: data.ok ? 'ok' : 'error',
      source: 'pages-function',
      cache_ttl_sec: Math.round(CACHE_TTL_MS / 1000),
      data,
    };
    const resp = json(body, data.ok ? 200 : 502);
    resp.headers.set('x-cache', 'MISS');
    // Store only successful payloads
    if (data.ok) {
      const toCache = resp.clone();
      // Cloudflare Cache API requires absolute URL Request
      await cache.put(new Request(CACHE_KEY), toCache);
    }
    return resp;
  } catch (err) {
    return json({
      status: 'error',
      source: 'pages-function',
      error: String(err && err.message ? err.message : err),
      data: { ok: false, source: 'implied_lease', error: String(err && err.message ? err.message : err) },
    }, 502, 'no-store');
  }
}
