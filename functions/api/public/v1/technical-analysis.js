// GET /api/public/v1/technical-analysis?market=a|hk|us|futures&s=SSE:600021,...
// TradingView scanner summaries proxied and cached at the Cloudflare edge.

const COLUMNS = ['Recommend.All|240', 'Recommend.All', 'Recommend.All|1W'];
const PERIODS = [
  { period: '4h', label: '4小时' },
  { period: '1d', label: '1天' },
  { period: '1W', label: '1周' },
];
const MARKET_CONFIG = {
  a: { scanner: 'china', exchanges: new Set(['SSE', 'SZSE', 'BSE']) },
  hk: { scanner: 'hongkong', exchanges: new Set(['HKEX']) },
  us: { scanner: 'america', exchanges: new Set(['NASDAQ', 'NYSE', 'AMEX']) },
  futures: { scanner: 'futures', exchanges: new Set(['COMEX', 'NYMEX', 'CME', 'CBOT']) },
};
const TTL_MS = 60_000;
const MAX_CACHE_ENTRIES = 64;
const cache = new Map();

function pruneCache(now = Date.now()) {
  for (const [key, entry] of cache) {
    if (entry.expiresAt <= now) cache.delete(key);
  }
  while (cache.size > MAX_CACHE_ENTRIES) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey == null) break;
    cache.delete(oldestKey);
  }
}

export function technicalAnalysisCacheSize() {
  pruneCache();
  return cache.size;
}

export function classifyRecommendation(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'UNAVAILABLE';
  if (value < -0.5) return 'STRONG_SELL';
  if (value < -0.1) return 'SELL';
  if (value <= 0.1) return 'NEUTRAL';
  if (value <= 0.5) return 'BUY';
  if (value <= 1) return 'STRONG_BUY';
  return 'UNAVAILABLE';
}

function marketName(value) {
  const raw = String(value || 'hk').trim().toLowerCase();
  return raw === 'cn' || raw === 'a-share' ? 'a' : raw;
}

function normalizeTicker(value, market) {
  const config = MARKET_CONFIG[market];
  const raw = String(value || '').trim().toUpperCase();
  if (!config || !raw) return null;

  // Backward-compatible Hong Kong shorthand: 01378 / 1378.HK.
  if (market === 'hk' && !raw.includes(':')) {
    const bare = raw.replace(/\.HK$/, '');
    if (!/^\d{1,5}$/.test(bare) || Number(bare) <= 0) return null;
    const tvCode = String(Number(bare));
    return { key: tvCode.padStart(5, '0'), ticker: `HKEX:${tvCode}` };
  }

  const match = raw.match(/^([A-Z]+):([A-Z0-9][A-Z0-9.!_=-]*)$/);
  if (!match || !config.exchanges.has(match[1])) return null;
  const ticker = `${match[1]}:${match[2]}`;
  return { key: ticker, ticker };
}

function json(payload, status = 200, ttl = 0) {
  const cacheControl = ttl > 0
    ? `public, max-age=${ttl}, s-maxage=${ttl}, stale-while-revalidate=${ttl}`
    : 'no-store';
  return Response.json(payload, { status, headers: { 'Cache-Control': cacheControl } });
}

export async function handleTechnicalAnalysis(request, fetchImpl = fetch) {
  const url = new URL(request.url);
  const market = marketName(url.searchParams.get('market'));
  const config = MARKET_CONFIG[market];
  if (!config) return json({ ok: false, error: 'invalid_market' }, 400);

  const parsed = String(url.searchParams.get('s') || '')
    .split(',')
    .map((value) => normalizeTicker(value, market))
    .filter(Boolean)
    .slice(0, 30);
  const symbols = [...new Map(parsed.map((item) => [item.key, item])).values()]
    .sort((a, b) => a.key.localeCompare(b.key));
  if (!symbols.length) return json({ ok: false, error: 'invalid_symbols' }, 400);

  pruneCache();
  const cacheKey = `${market}:${symbols.map((item) => item.key).join(',')}`;
  const cached = cache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return json(cached.payload, 200, 60);

  const scanPayload = {
    symbols: {
      tickers: symbols.map((item) => item.ticker),
      query: { types: [] },
    },
    columns: COLUMNS,
  };

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 7000);
    let response;
    try {
      response = await fetchImpl(`https://scanner.tradingview.com/${config.scanner}/scan`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 ETF-Compass/1.0',
        },
        body: JSON.stringify(scanPayload),
        signal: controller.signal,
        cf: { cacheTtl: 60, cacheEverything: true },
      });
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) throw new Error(`TradingView ${response.status}`);
    const upstream = await response.json();
    const byTicker = new Map((upstream?.data || []).map((row) => [String(row?.s || '').toUpperCase(), row?.d || []]));
    const results = {};
    for (const symbol of symbols) {
      const values = byTicker.get(symbol.ticker) || [];
      results[symbol.key] = PERIODS.map((period, index) => ({
        ...period,
        score: typeof values[index] === 'number' && Number.isFinite(values[index]) ? values[index] : null,
        recommendation: classifyRecommendation(values[index]),
      }));
    }
    const body = {
      ok: true,
      source: 'tradingview_scanner',
      market,
      generated_at: new Date().toISOString(),
      cache_seconds: 60,
      results,
    };
    if (!cache.has(cacheKey) && cache.size >= MAX_CACHE_ENTRIES) {
      const oldestKey = cache.keys().next().value;
      if (oldestKey != null) cache.delete(oldestKey);
    }
    cache.set(cacheKey, { expiresAt: Date.now() + TTL_MS, payload: body });
    return json(body, 200, 60);
  } catch (error) {
    return json({ ok: false, error: 'upstream_unavailable', reason: String(error?.message || error) }, 503);
  }
}

export async function onRequestGet({ request }) {
  return handleTechnicalAnalysis(request);
}
