// GET /api/public/v1/technical-analysis?s=01378,06809
// TradingView public scanner summary for Hong Kong rolling cards.

const SCAN_URL = 'https://scanner.tradingview.com/hongkong/scan';
const COLUMNS = ['Recommend.All|240', 'Recommend.All', 'Recommend.All|1W'];
const PERIODS = [
  { period: '4h', label: '4小时' },
  { period: '1d', label: '1天' },
  { period: '1W', label: '1周' },
];
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

function normalizeHongKongCode(value) {
  const raw = String(value || '').trim().toUpperCase().replace(/\.HK$/, '');
  if (!/^\d{1,5}$/.test(raw) || Number(raw) <= 0) return null;
  const tv = String(Number(raw));
  return { internal: tv.padStart(5, '0'), tv };
}

function json(payload, status = 200, ttl = 0) {
  return Response.json(payload, {
    status,
    headers: {
      'Cache-Control': ttl > 0 ? `public, max-age=${ttl}` : 'no-store',
    },
  });
}

export async function handleTechnicalAnalysis(request, fetchImpl = fetch) {
  const url = new URL(request.url);
  const parsed = String(url.searchParams.get('s') || '')
    .split(',')
    .map(normalizeHongKongCode)
    .filter(Boolean)
    .slice(0, 30);
  const symbols = [...new Map(parsed.map((item) => [item.internal, item])).values()]
    .sort((a, b) => a.internal.localeCompare(b.internal));
  if (!symbols.length) return json({ ok: false, error: 'invalid_symbols' }, 400);

  pruneCache();
  const cacheKey = symbols.map((item) => item.internal).join(',');
  const cached = cache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return json(cached.payload, 200, 60);

  const payload = {
    symbols: {
      tickers: symbols.map((item) => `HKEX:${item.tv}`),
      query: { types: [] },
    },
    columns: COLUMNS,
  };

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 7000);
    let response;
    try {
      response = await fetchImpl(SCAN_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 ETF-Compass/1.0',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
        cf: { cacheTtl: 60, cacheEverything: true },
      });
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) throw new Error(`TradingView ${response.status}`);
    const upstream = await response.json();
    const byTv = new Map((upstream?.data || []).map((row) => [String(row?.s || '').split(':').pop(), row?.d || []]));
    const results = {};
    for (const symbol of symbols) {
      const values = byTv.get(symbol.tv) || [];
      results[symbol.internal] = PERIODS.map((period, index) => ({
        ...period,
        score: typeof values[index] === 'number' && Number.isFinite(values[index]) ? values[index] : null,
        recommendation: classifyRecommendation(values[index]),
      }));
    }
    const body = { ok: true, source: 'tradingview_scanner', generated_at: new Date().toISOString(), results };
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
