/**
 * Cloudflare Worker / Pages Function - Edge Stock & Futures Quote API
 * 支持 A股、港股、美股及期货全市场极速行情，具备【腾讯 -> 新浪 -> 雪球】三源全自动降级容错
 */

let cachedXqToken = null;
let xqTokenExpireAt = 0;

/** Short cache for identical quote batches (L1 isolate Map + L2 caches.default). */
export const QUOTE_CACHE_TTL_MS = 4000; // legacy alias: open-session baseline
export const QUOTE_CACHE_TTL_OPEN_MS = 4000;
export const QUOTE_CACHE_TTL_CLOSED_MS = 30000;
export const QUOTE_CACHE_TTL_WEEKEND_MS = 60000;
const QUOTE_CACHE_MAX_ENTRIES = 200;
const quoteCache = new Map(); // key -> { expiresAt, payload, source, storedAt, ttlMs }
const quoteCacheStats = { hit: 0, miss: 0, store: 0, edge_hit: 0, memory_hit: 0 };

export function clearQuoteCache() {
  quoteCache.clear();
  quoteCacheStats.hit = 0;
  quoteCacheStats.miss = 0;
  quoteCacheStats.store = 0;
  quoteCacheStats.edge_hit = 0;
  quoteCacheStats.memory_hit = 0;
}

/**
 * Session-aware TTL.
 * Open windows stay short for freshness; closed/weekend can reuse longer.
 * Markets covered loosely: CN (A/HK) + US regular hours in local zones.
 */
export function resolveQuoteCacheTtlMs(now = new Date()) {
  const cnParts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .filter((p) => p.type !== 'literal')
      .map((p) => [p.type, p.value]),
  );
  const usParts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .filter((p) => p.type !== 'literal')
      .map((p) => [p.type, p.value]),
  );

  const cnWeekend = cnParts.weekday === 'Sat' || cnParts.weekday === 'Sun';
  const usWeekend = usParts.weekday === 'Sat' || usParts.weekday === 'Sun';
  const cnMinutes = Number(cnParts.hour) * 60 + Number(cnParts.minute);
  const usMinutes = Number(usParts.hour) * 60 + Number(usParts.minute);

  // A-share regular: 09:15-11:30, 13:00-15:05 (include call auction / close tail)
  const cnOpen = !cnWeekend && (
    (cnMinutes >= 9 * 60 + 15 && cnMinutes < 11 * 60 + 30)
    || (cnMinutes >= 13 * 60 && cnMinutes < 15 * 60 + 5)
  );
  // US regular: 09:30-16:00 ET
  const usOpen = !usWeekend && usMinutes >= 9 * 60 + 30 && usMinutes < 16 * 60;

  if (cnOpen || usOpen) {
    return {
      ttlMs: QUOTE_CACHE_TTL_OPEN_MS,
      session: cnOpen && usOpen ? 'open_overlap' : (cnOpen ? 'open_cn' : 'open_us'),
    };
  }
  if (cnWeekend && usWeekend) {
    return { ttlMs: QUOTE_CACHE_TTL_WEEKEND_MS, session: 'weekend' };
  }
  return { ttlMs: QUOTE_CACHE_TTL_CLOSED_MS, session: 'closed' };
}

export function getQuoteCacheStats() {
  const policy = resolveQuoteCacheTtlMs();
  return {
    ...quoteCacheStats,
    size: quoteCache.size,
    ttl_ms: policy.ttlMs,
    session: policy.session,
    open_ttl_ms: QUOTE_CACHE_TTL_OPEN_MS,
    closed_ttl_ms: QUOTE_CACHE_TTL_CLOSED_MS,
    weekend_ttl_ms: QUOTE_CACHE_TTL_WEEKEND_MS,
  };
}

function normalizeQuoteCacheKey(symbolsStr, exchange) {
  const items = String(symbolsStr || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 50)
    .sort((a, b) => a.localeCompare(b));
  return `${String(exchange || 'SSE').toUpperCase()}|${items.join(',')}`;
}

function quoteCacheRequest(key) {
  // Stable synthetic URL for Cache API matching (ignore client query noise).
  return new Request(`https://quote-cache.internal/v1?k=${encodeURIComponent(key)}`, {
    method: 'GET',
  });
}

function getEdgeCache() {
  try {
    if (typeof caches !== 'undefined' && caches?.default) return caches.default;
  } catch (_) {
    // unit tests / non-CF runtimes
  }
  return null;
}

function readMemoryQuoteCache(key, nowMs = Date.now()) {
  const row = quoteCache.get(key);
  if (!row) return null;
  if (nowMs >= row.expiresAt) {
    quoteCache.delete(key);
    return null;
  }
  // refresh LRU order
  quoteCache.delete(key);
  quoteCache.set(key, row);
  return row;
}

function writeMemoryQuoteCache(key, payload, storedAt = Date.now(), ttlMs = QUOTE_CACHE_TTL_OPEN_MS) {
  if (!payload || payload.status !== 'ok') return;
  if (quoteCache.has(key)) quoteCache.delete(key);
  quoteCache.set(key, {
    payload,
    source: payload.source || 'unknown',
    storedAt,
    ttlMs,
    expiresAt: storedAt + ttlMs,
  });
  while (quoteCache.size > QUOTE_CACHE_MAX_ENTRIES) {
    const oldest = quoteCache.keys().next().value;
    quoteCache.delete(oldest);
  }
}

async function readEdgeQuoteCache(key, nowMs = Date.now()) {
  const edge = getEdgeCache();
  if (!edge) return null;
  try {
    const cached = await edge.match(quoteCacheRequest(key));
    if (!cached || !cached.ok) return null;
    const storedAt = Number(cached.headers.get('x-quote-stored-at') || 0);
    const ttlMs = Number(cached.headers.get('x-quote-ttl-ms') || QUOTE_CACHE_TTL_OPEN_MS);
    if (storedAt && nowMs - storedAt >= ttlMs) {
      // best-effort expire; Cache API TTL is controlled via cache-control below
      return null;
    }
    const payload = await cached.json();
    if (!payload || payload.status !== 'ok') return null;
    const source = cached.headers.get('x-quote-source') || payload.source || 'unknown';
    // promote to L1 with remaining lifetime
    writeMemoryQuoteCache(key, payload, storedAt || nowMs, ttlMs);
    return {
      payload,
      source,
      storedAt: storedAt || nowMs,
      ttlMs,
      layer: 'edge',
    };
  } catch (_) {
    return null;
  }
}

async function writeEdgeQuoteCache(key, payload, storedAt = Date.now(), ttlMs = QUOTE_CACHE_TTL_OPEN_MS) {
  const edge = getEdgeCache();
  if (!edge || !payload || payload.status !== 'ok') return false;
  try {
    const ttlSec = Math.max(1, Math.ceil(ttlMs / 1000));
    const response = new Response(JSON.stringify(payload), {
      status: 200,
      headers: {
        'content-type': 'application/json; charset=utf-8',
        // Cloudflare Cache API honors this for edge object lifetime.
        'cache-control': `public, max-age=${ttlSec}`,
        'x-quote-stored-at': String(storedAt),
        'x-quote-ttl-ms': String(ttlMs),
        'x-quote-source': String(payload.source || 'unknown'),
      },
    });
    await edge.put(quoteCacheRequest(key), response);
    return true;
  } catch (_) {
    return false;
  }
}

async function readQuoteCache(key) {
  const nowMs = Date.now();
  const mem = readMemoryQuoteCache(key, nowMs);
  if (mem) return { ...mem, layer: 'memory' };
  return readEdgeQuoteCache(key, nowMs);
}

async function writeQuoteCache(key, payload, ttlMs = QUOTE_CACHE_TTL_OPEN_MS) {
  if (!payload || payload.status !== 'ok') return;
  const storedAt = Date.now();
  writeMemoryQuoteCache(key, payload, storedAt, ttlMs);
  const edgeOk = await writeEdgeQuoteCache(key, payload, storedAt, ttlMs);
  quoteCacheStats.store += 1;
  return edgeOk;
}

/**
 * 自动获取雪球访客 Token (xq_a_token)
 */
export async function getXueqiuToken() {
  const now = Date.now();
  if (cachedXqToken && now < xqTokenExpireAt) {
    return cachedXqToken;
  }

  try {
    const res = await fetch('https://xueqiu.com/about', {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      },
    });

    const setCookie = res.headers.get('set-cookie') || '';
    const match = setCookie.match(/xq_a_token=([^;]+)/);

    if (match && match[1]) {
      cachedXqToken = match[1];
      xqTokenExpireAt = now + 15 * 60 * 1000;
      return cachedXqToken;
    }
  } catch (e) {
    console.error('Failed to auto fetch Xueqiu token:', e.message);
  }

  return null;
}

/**
 * Symbol 代码自动解析与映射
 */
export function parseSymbol(rawSymbol, defaultExchange = 'SSE') {
  const s = rawSymbol.trim();
  if (!s) return null;

  if (s.includes('.')) {
    const parts = s.split('.');
    const code = parts[0];
    const ex = parts[1].toUpperCase();

    if (ex === 'HK') {
      const normalized = /^\d+$/.test(code) ? code.padStart(5, '0') : code.toUpperCase();
      return { tencent: `hk${normalized}`, sina: `hk${normalized}`, xueqiu: normalized, displayCode: normalized, type: 'hk' };
    }
    if (ex === 'US') {
      const normalized = code.replace(/^\./, '').toUpperCase();
      return { tencent: `us${normalized}`, sina: `gb_${normalized.toLowerCase()}`, xueqiu: normalized, displayCode: normalized, type: 'us' };
    }
    if (ex === 'SZ' || ex === 'SZSE') return { tencent: `sz${code}`, sina: `sz${code}`, xueqiu: `SZ${code}`, displayCode: code, type: 'a' };
    if (ex === 'SH' || ex === 'SSE') return { tencent: `sh${code}`, sina: `sh${code}`, xueqiu: `SH${code}`, displayCode: code, type: 'a' };
    if (ex === 'BJ') return { tencent: `bj${code}`, sina: `bj${code}`, xueqiu: `BJ${code}`, displayCode: code, type: 'a' };
  }

  if (s.startsWith('hk')) return { tencent: s, sina: s, xueqiu: s.slice(2), displayCode: s.slice(2), type: 'hk' };
  if (s.startsWith('us')) return { tencent: s, sina: `gb_${s.slice(2).toLowerCase()}`, xueqiu: s.slice(2).toUpperCase(), displayCode: s.slice(2), type: 'us' };
  if (s.startsWith('hf_') || s.startsWith('nf_')) return { tencent: s, sina: s, xueqiu: s, displayCode: s, type: 'futures' };
  if (s.startsWith('sh') || s.startsWith('sz') || s.startsWith('bj')) return { tencent: s, sina: s, xueqiu: s.toUpperCase(), displayCode: s.slice(2), type: 'a' };

  // Dollar Index (Sina DINIW). Tencent has no usable code; keep a pass-through for batching.
  const upper = s.toUpperCase();
  if (upper === 'DINIW' || upper === 'DXY' || upper === 'USDINDEX') {
    return { tencent: 'DINIW', sina: 'DINIW', xueqiu: 'DINIW', displayCode: 'DINIW', type: 'fx' };
  }

  if (/^\d{5}$/.test(s)) return { tencent: `hk${s}`, sina: `hk${s}`, xueqiu: s, displayCode: s, type: 'hk' };
  if (/^[A-Za-z]{1,5}$/.test(s)) return { tencent: `us${s}`, sina: `gb_${s.toLowerCase()}`, xueqiu: s.toUpperCase(), displayCode: s.toUpperCase(), type: 'us' };

  if (/^\d{6}$/.test(s)) {
    const isSZ = defaultExchange === 'SZSE' || s.startsWith('159') || s.startsWith('300') || s.startsWith('00') || s.startsWith('399');
    const prefix = isSZ ? 'sz' : 'sh';
    return { tencent: `${prefix}${s}`, sina: `${prefix}${s}`, xueqiu: `${prefix.toUpperCase()}${s}`, displayCode: s, type: 'a' };
  }

  return { tencent: s, sina: s, xueqiu: s, displayCode: s, type: 'unknown' };
}

/**
 * 1️⃣ 主数据源：腾讯行情 (qt.gtimg.cn)
 */
async function fetchTencent(parsedList) {
  const secCodes = parsedList.map(p => p.tencent);
  const upstreamUrl = `https://qt.gtimg.cn/q=${secCodes.join(',')}`;
  
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);

  const upstreamRes = await fetch(upstreamUrl, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Referer': 'https://finance.qq.com/',
    },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));

  if (!upstreamRes.ok) throw new Error(`Tencent HTTP ${upstreamRes.status}`);

  const buffer = await upstreamRes.arrayBuffer();
  const decoder = new TextDecoder('gbk');
  const text = decoder.decode(buffer);

  const quotes = {};
  const statements = text.split(';');

  for (const stmt of statements) {
    const futuresMatch = stmt.match(/v_([hn]f_[A-Za-z0-9_]+)="([^"]+)"/);
    if (futuresMatch) {
      const secKey = futuresMatch[1];
      const parts = futuresMatch[2].split(',');
      if (parts.length >= 14) {
        const price = parseFloat(parts[0]) || 0;
        const changeAmount = parseFloat(parts[1]) || 0;
        const openPrice = parseFloat(parts[2]) || 0;
        const prevClose = parseFloat(parts[7]) || 0;
        const highPrice = parseFloat(parts[4]) || 0;
        const lowPrice = parseFloat(parts[5]) || 0;
        const name = parts[13] || secKey;
        const dateStr = parts[12] || '';
        const timeStr = parts[6] || '';
        if (!(price > 0)) continue;
        const changePercent = prevClose ? parseFloat(((changeAmount / prevClose) * 100).toFixed(2)) : 0;

        quotes[secKey] = {
          symbol: secKey,
          sec_code: secKey,
          name,
          market: 'FUTURES',
          price,
          prev_close: prevClose,
          open: openPrice,
          high: highPrice,
          low: lowPrice,
          change_amount: parseFloat(changeAmount.toFixed(3)),
          change_percent: changePercent,
          quote_time: dateStr && timeStr ? `${dateStr}T${timeStr}+08:00` : new Date().toISOString(),
          source: 'tencent',
          status: 'ok',
        };
        continue;
      }
    }

    const stockMatch = stmt.match(/v_([a-zA-Z0-9_]+)="([^"]+)"/);
    if (!stockMatch) continue;

    const secKey = stockMatch[1];
    const parts = stockMatch[2].split('~');
    if (parts.length < 33) continue;

    const name = parts[1] || '';
    const code = parts[2] || secKey;
    const currentPrice = parseFloat(parts[3]) || 0;
    const prevClose = parseFloat(parts[4]) || 0;
    const openPrice = parseFloat(parts[5]) || 0;
    const volume = parseInt(parts[6], 10) || 0;
    const changeAmount = parseFloat(parts[31]) || (currentPrice - prevClose);
    const changePercent = parseFloat(parts[32]) || 0;
    const highPrice = parseFloat(parts[33]) || 0;
    const lowPrice = parseFloat(parts[34]) || 0;
    const rawTime = parts[30] || '';

    let market = 'A-SHARE';
    if (secKey.startsWith('hk')) market = 'HK-SHARE';
    else if (secKey.startsWith('us')) market = 'US-SHARE';

    let quoteTime = new Date().toISOString();
    if (rawTime && rawTime.length === 14) {
      quoteTime = `${rawTime.slice(0, 4)}-${rawTime.slice(4, 6)}-${rawTime.slice(6, 8)}T${rawTime.slice(8, 10)}:${rawTime.slice(10, 12)}:${rawTime.slice(12, 14)}+08:00`;
    }

    quotes[code] = {
      symbol: code,
      sec_code: secKey,
      name,
      market,
      price: currentPrice,
      prev_close: prevClose,
      open: openPrice,
      high: highPrice,
      low: lowPrice,
      change_amount: parseFloat(changeAmount.toFixed(3)),
      change_percent: parseFloat(changePercent.toFixed(2)),
      volume_hands: volume,
      quote_time: quoteTime,
      source: 'tencent',
      status: 'ok',
    };
  }

  return quotes;
}

async function fetchHangSengComposite() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);
  const response = await fetch('https://www.hsi.com.hk/data/eng/rt/dashboard.do?5500', {
    headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json' },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));
  if (!response.ok) throw new Error(`HSI dashboard HTTP ${response.status}`);
  const data = await response.json();
  const hongKong = (data?.regions || []).find(region => region?.regionId === 'hongkong');
  const item = (hongKong?.dashboardList || []).find(index => index?.url === 'hsci' || index?.indexCode === '00011.00');
  if (!item) throw new Error('Hang Seng Composite Index missing');
  const price = Number(item.indexValue);
  const prevClose = Number(item.previousClose);
  const changeAmount = Number(item.changeValue);
  const changePercent = Number(item.changePercentage);
  if (!(price > 0)) throw new Error('Hang Seng Composite Index invalid price');
  return {
    HSCI: {
      symbol: 'HSCI',
      sec_code: 'hsi:00011.00',
      name: '恒生综合指数',
      market: 'HK-SHARE',
      price,
      prev_close: prevClose,
      open: 0,
      high: 0,
      low: 0,
      change_amount: Number(changeAmount.toFixed(3)),
      change_percent: Number(changePercent.toFixed(2)),
      quote_time: item.lastUpdate ? `${item.lastUpdate.replace(' ', 'T')}+08:00` : new Date().toISOString(),
      source: 'hang-seng-indexes',
      status: 'ok',
    },
  };
}

/**
 * 2️⃣ 备用数据源 1：新浪行情 (hq.sinajs.cn)
 */
async function fetchSina(parsedList) {
  const secCodes = parsedList.map(p => p.sina);
  const upstreamUrl = `https://hq.sinajs.cn/list=${secCodes.join(',')}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);

  const upstreamRes = await fetch(upstreamUrl, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Referer': 'https://finance.sina.com.cn/',
    },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));

  if (!upstreamRes.ok) throw new Error(`Sina HTTP ${upstreamRes.status}`);

  const buffer = await upstreamRes.arrayBuffer();
  const decoder = new TextDecoder('gbk');
  const text = decoder.decode(buffer);

  const quotes = {};
  const lines = text.split('\n');

  for (const line of lines) {
    const match = line.match(/hq_str_([a-zA-Z0-9_]+)=\"([^\"]+)\"/);
    if (!match) continue;

    const secKey = match[1];
    const parts = match[2].split(',');
    if (parts.length < 8) continue;

    // Domestic continuous futures: nf_AU0 / nf_SC0 ...
    // Sina layout: name,time,open,high,low,...,buy,sell,last,...,prevSettle,...,date
    if (secKey.startsWith('nf_') || secKey.startsWith('hf_')) {
      const name = parts[0] || secKey;
      const openPrice = parseFloat(parts[2]) || 0;
      const highPrice = parseFloat(parts[3]) || 0;
      const lowPrice = parseFloat(parts[4]) || 0;
      // Prefer last trade; fall back to sell/buy when market is quiet.
      const last = parseFloat(parts[8]) || parseFloat(parts[7]) || parseFloat(parts[6]) || 0;
      const prevClose = parseFloat(parts[10]) || parseFloat(parts[5]) || 0;
      const price = last || prevClose;
      if (!(price > 0)) continue;
      const changePercent = prevClose
        ? parseFloat((((price - prevClose) / prevClose) * 100).toFixed(2))
        : 0;
      const dateStr = parts[17] || '';
      const timeRaw = (parts[1] || '').padStart(6, '0');
      const quoteTime = dateStr && timeRaw.length === 6
        ? `${dateStr}T${timeRaw.slice(0, 2)}:${timeRaw.slice(2, 4)}:${timeRaw.slice(4, 6)}+08:00`
        : new Date().toISOString();

      quotes[secKey] = {
        symbol: secKey,
        sec_code: secKey,
        name,
        market: 'FUTURES',
        price,
        prev_close: prevClose,
        open: openPrice,
        high: highPrice,
        low: lowPrice,
        change_amount: parseFloat((price - prevClose).toFixed(3)),
        change_percent: changePercent,
        quote_time: quoteTime,
        source: 'sina',
        status: 'ok',
      };
      continue;
    }

    // Dollar Index (Sina DINIW):
    // time,price,price,open,?,prevClose,high,low,price,name,date
    if (secKey.toUpperCase() === 'DINIW') {
      const timeStr = parts[0] || '';
      const price = parseFloat(parts[1]) || parseFloat(parts[8]) || 0;
      const openPrice = parseFloat(parts[3]) || 0;
      const prevClose = parseFloat(parts[5]) || 0;
      const highPrice = parseFloat(parts[6]) || 0;
      const lowPrice = parseFloat(parts[7]) || 0;
      const name = parts[9] || '美元指数';
      const dateStr = parts[10] || '';
      if (!(price > 0)) continue;
      const changeAmount = prevClose ? price - prevClose : 0;
      const changePercent = prevClose
        ? parseFloat((((price - prevClose) / prevClose) * 100).toFixed(2))
        : 0;
      const quoteTime = dateStr && /^\d{2}:\d{2}:\d{2}$/.test(timeStr)
        ? `${dateStr}T${timeStr}+08:00`
        : new Date().toISOString();
      quotes.DINIW = {
        symbol: 'DINIW',
        sec_code: 'DINIW',
        name,
        market: 'FX',
        price: parseFloat(price.toFixed(4)),
        prev_close: prevClose ? parseFloat(prevClose.toFixed(4)) : 0,
        open: openPrice ? parseFloat(openPrice.toFixed(4)) : 0,
        high: highPrice ? parseFloat(highPrice.toFixed(4)) : 0,
        low: lowPrice ? parseFloat(lowPrice.toFixed(4)) : 0,
        change_amount: parseFloat(changeAmount.toFixed(4)),
        change_percent: changePercent,
        quote_time: quoteTime,
        source: 'sina',
        status: 'ok',
      };
      continue;
    }

    const name = parts[0];
    let price = 0, openPrice = 0, prevClose = 0, highPrice = 0, lowPrice = 0, changePercent = 0, code = secKey;

    if (secKey.startsWith('sh') || secKey.startsWith('sz') || secKey.startsWith('bj')) {
      openPrice = parseFloat(parts[1]) || 0;
      prevClose = parseFloat(parts[2]) || 0;
      price = parseFloat(parts[3]) || 0;
      highPrice = parseFloat(parts[4]) || 0;
      lowPrice = parseFloat(parts[5]) || 0;
      code = secKey.slice(2);
      changePercent = prevClose ? parseFloat((((price - prevClose) / prevClose) * 100).toFixed(2)) : 0;
    } else if (secKey.startsWith('gb_')) {
      price = parseFloat(parts[1]) || 0;
      changePercent = parseFloat(parts[2]) || 0;
      openPrice = parseFloat(parts[5]) || 0;
      highPrice = parseFloat(parts[6]) || 0;
      lowPrice = parseFloat(parts[7]) || 0;
      prevClose = parseFloat(parts[26]) || 0;
      code = secKey.slice(3).toUpperCase();
    } else {
      continue;
    }

    if (!(price > 0)) continue;

    quotes[code] = {
      symbol: code,
      sec_code: secKey,
      name,
      price,
      prev_close: prevClose,
      open: openPrice,
      high: highPrice,
      low: lowPrice,
      change_amount: parseFloat((price - prevClose).toFixed(3)),
      change_percent: changePercent,
      quote_time: new Date().toISOString(),
      source: 'sina',
      status: 'ok',
    };
  }

  return quotes;
}

/**
 * 3️⃣ 备用数据源 2：雪球 API (stock.xueqiu.com，自动抓取 Token)
 */
async function fetchXueqiu(parsedList) {
  const token = await getXueqiuToken();
  if (!token) throw new Error('Could not get Xueqiu guest token');

  const symbols = parsedList.map(p => p.xueqiu).join(',');
  const upstreamUrl = `https://stock.xueqiu.com/v5/stock/realtime/quotec.json?symbol=${symbols}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);

  const upstreamRes = await fetch(upstreamUrl, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Cookie': `xq_a_token=${token}`,
      'Referer': 'https://xueqiu.com/',
    },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));

  if (!upstreamRes.ok) throw new Error(`Xueqiu HTTP ${upstreamRes.status}`);

  const data = await upstreamRes.json();
  const items = data.data || [];
  const quotes = {};

  for (const item of items) {
    const rawSymbol = item.symbol || '';
    const code = rawSymbol.replace(/^(SH|SZ|BJ)/i, '');
    const price = item.current || 0;
    const prevClose = item.last_close || 0;

    quotes[code] = {
      symbol: code,
      sec_code: rawSymbol,
      name: item.name || code,
      price,
      prev_close: prevClose,
      open: item.open || 0,
      high: item.high || 0,
      low: item.low || 0,
      change_amount: parseFloat((item.chg || price - prevClose).toFixed(3)),
      change_percent: parseFloat((item.percent || 0).toFixed(2)),
      quote_time: item.time ? new Date(item.time).toISOString() : new Date().toISOString(),
      source: 'xueqiu',
      status: 'ok',
    };
  }

  return quotes;
}

/**
 * 核心调度：【腾讯 -> 新浪 -> 雪球】三级自动降级
 */
export async function fetchQuote(symbolsStr, defaultExchange = 'SSE') {
  const rawItems = (symbolsStr || '600021').split(',').map(s => s.trim()).filter(Boolean).slice(0, 50);
  if (rawItems.length === 0) return { status: 'error', message: 'no symbols provided' };

  const parsedList = rawItems.map(item => parseSymbol(item, defaultExchange)).filter(Boolean);
  const hasValidPrice = (quotes) => Object.values(quotes || {}).some((q) => Number(q?.price) > 0);
  const needsDollarIndex = parsedList.some((item) => String(item.displayCode || '').toUpperCase() === 'DINIW');
  const hasDollarIndex = (quotes) => Number(quotes?.DINIW?.price) > 0;
  const mergeDollarIndexFromSina = async (quotes) => {
    if (!needsDollarIndex || hasDollarIndex(quotes)) return quotes;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const sinaQuotes = await fetchSina(parsedList.filter((item) => String(item.displayCode || '').toUpperCase() === 'DINIW'));
        if (hasDollarIndex(sinaQuotes)) {
          return { ...quotes, DINIW: sinaQuotes.DINIW };
        }
      } catch (err) {
        console.warn('Sina dollar-index fill failed:', err.message);
      }
    }
    return quotes;
  };

  // 1. 尝试腾讯；恒生综合指数由恒生指数公司官方看板补齐。
  try {
    let quotes = await fetchTencent(parsedList);
    if (rawItems.some(item => item.toUpperCase() === 'HSCI.HK' || item.toUpperCase() === 'HKHSCI')) {
      try {
        Object.assign(quotes, await fetchHangSengComposite());
      } catch (err) {
        console.warn('Hang Seng Composite official source failed:', err.message);
      }
    }
    quotes = await mergeDollarIndexFromSina(quotes);
    if (hasValidPrice(quotes)) {
      const sources = new Set(Object.values(quotes).map(quote => quote.source).filter(Boolean));
      const source = sources.size > 1 ? 'mixed' : (sources.values().next().value || 'tencent');
      return { status: 'ok', source, count: Object.keys(quotes).length, quotes };
    }
  } catch (err) {
    console.warn('Primary source (Tencent) failed, falling back to Sina:', err.message);
  }

  // 2. 尝试新浪
  try {
    const quotes = await fetchSina(parsedList);
    if (hasValidPrice(quotes)) {
      return { status: 'ok', source: 'sina', count: Object.keys(quotes).length, quotes };
    }
  } catch (err) {
    console.warn('Fallback 1 (Sina) failed, falling back to Xueqiu:', err.message);
  }

  // 3. 尝试雪球 (自动 Token)
  try {
    const quotes = await fetchXueqiu(parsedList);
    if (hasValidPrice(quotes)) {
      return { status: 'ok', source: 'xueqiu', count: Object.keys(quotes).length, quotes };
    }
  } catch (err) {
    console.error('Fallback 2 (Xueqiu) failed:', err.message);
  }

  throw new Error('All upstream quote sources (Tencent, Sina, Xueqiu) failed');
}

/**
 * Cloudflare Pages Functions entry (functions/api/public/v1/quote.js)
 * and Workers entry (export default.fetch) share this handler.
 */
export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const symbols = url.searchParams.get('symbols') || url.searchParams.get('symbol') || '600021';
  const exchange = (url.searchParams.get('exchange') || 'SSE').toUpperCase();
  const bypassCache = url.searchParams.get('nocache') === '1' || url.searchParams.get('refresh') === '1';
  const cacheKey = normalizeQuoteCacheKey(symbols, exchange);
  const cachePolicy = resolveQuoteCacheTtlMs();

  const headers = {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=5, s-maxage=5, stale-while-revalidate=15',
    'x-content-type-options': 'nosniff',
    'access-control-allow-origin': '*',
    'x-quote-cache-ttl-ms': String(cachePolicy.ttlMs),
    'x-quote-cache-session': cachePolicy.session,
  };

  try {
    if (!bypassCache) {
      const hit = await readQuoteCache(cacheKey);
      if (hit) {
        quoteCacheStats.hit += 1;
        if (hit.layer === 'edge') quoteCacheStats.edge_hit += 1;
        if (hit.layer === 'memory') quoteCacheStats.memory_hit += 1;
        const ageMs = Math.max(0, Date.now() - hit.storedAt);
        headers['x-quote-cache'] = 'HIT';
        headers['x-quote-cache-layer'] = hit.layer || 'memory';
        headers['x-quote-cache-age-ms'] = String(ageMs);
        headers['x-quote-source'] = String(hit.source || hit.payload?.source || 'unknown');
        if (hit.ttlMs) headers['x-quote-cache-ttl-ms'] = String(hit.ttlMs);
        console.log(JSON.stringify({
          event: 'quote_cache',
          result: 'HIT',
          layer: hit.layer || 'memory',
          key: cacheKey,
          age_ms: ageMs,
          ttl_ms: hit.ttlMs || cachePolicy.ttlMs,
          session: cachePolicy.session,
          source: hit.source,
          count: hit.payload?.count,
        }));
        return new Response(JSON.stringify(hit.payload), {
          status: 200,
          headers,
        });
      }
    }

    quoteCacheStats.miss += 1;
    const data = await fetchQuote(symbols, exchange);
    if (!bypassCache && data?.status === 'ok') {
      await writeQuoteCache(cacheKey, data, cachePolicy.ttlMs);
    }
    headers['x-quote-cache'] = bypassCache ? 'BYPASS' : 'MISS';
    headers['x-quote-cache-layer'] = 'none';
    headers['x-quote-cache-age-ms'] = '0';
    headers['x-quote-source'] = String(data?.source || 'unknown');
    console.log(JSON.stringify({
      event: 'quote_cache',
      result: bypassCache ? 'BYPASS' : 'MISS',
      layer: 'none',
      key: cacheKey,
      ttl_ms: cachePolicy.ttlMs,
      session: cachePolicy.session,
      source: data?.source || null,
      count: data?.count || 0,
      status: data?.status || 'error',
    }));
    return new Response(JSON.stringify(data), {
      status: data.status === 'ok' ? 200 : 400,
      headers,
    });
  } catch (err) {
    headers['x-quote-cache'] = 'ERROR';
    headers['x-quote-cache-layer'] = 'none';
    headers['x-quote-source'] = 'none';
    console.error(JSON.stringify({
      event: 'quote_cache',
      result: 'ERROR',
      key: cacheKey,
      session: cachePolicy.session,
      message: err.message || 'internal error',
    }));
    return new Response(JSON.stringify({ status: 'error', message: err.message || 'internal error' }), {
      status: 500,
      headers,
    });
  }
}

export default {
  async fetch(request) {
    return onRequestGet({ request });
  },
};
