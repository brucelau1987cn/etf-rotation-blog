/**
 * Cloudflare Worker / Pages Function - Edge Stock & Futures Quote API
 * 支持 A股、港股、美股及期货全市场极速行情，具备【腾讯 -> 新浪 -> 雪球】三源全自动降级容错
 */

let cachedXqToken = null;
let xqTokenExpireAt = 0;

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

    if (ex === 'HK') return { tencent: `hk${code.padStart(5, '0')}`, sina: `hk${code.padStart(5, '0')}`, xueqiu: `${code.padStart(5, '0')}`, displayCode: code, type: 'hk' };
    if (ex === 'US') return { tencent: `us${code}`, sina: `gb_${code.toLowerCase()}`, xueqiu: code.toUpperCase(), displayCode: code, type: 'us' };
    if (ex === 'SZ' || ex === 'SZSE') return { tencent: `sz${code}`, sina: `sz${code}`, xueqiu: `SZ${code}`, displayCode: code, type: 'a' };
    if (ex === 'SH' || ex === 'SSE') return { tencent: `sh${code}`, sina: `sh${code}`, xueqiu: `SH${code}`, displayCode: code, type: 'a' };
    if (ex === 'BJ') return { tencent: `bj${code}`, sina: `bj${code}`, xueqiu: `BJ${code}`, displayCode: code, type: 'a' };
  }

  if (s.startsWith('hk')) return { tencent: s, sina: s, xueqiu: s.slice(2), displayCode: s.slice(2), type: 'hk' };
  if (s.startsWith('us')) return { tencent: s, sina: `gb_${s.slice(2).toLowerCase()}`, xueqiu: s.slice(2).toUpperCase(), displayCode: s.slice(2), type: 'us' };
  if (s.startsWith('hf_') || s.startsWith('nf_')) return { tencent: s, sina: s, xueqiu: s, displayCode: s, type: 'futures' };
  if (s.startsWith('sh') || s.startsWith('sz') || s.startsWith('bj')) return { tencent: s, sina: s, xueqiu: s.toUpperCase(), displayCode: s.slice(2), type: 'a' };

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
    const match = line.match(/hq_str_([a-zA-Z0-9_]+)="([^"]+)"/);
    if (!match) continue;

    const secKey = match[1];
    const parts = match[2].split(',');
    if (parts.length < 8) continue;

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
    }

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

  // 1. 尝试腾讯
  try {
    const quotes = await fetchTencent(parsedList);
    if (Object.keys(quotes).length > 0) {
      return { status: 'ok', source: 'tencent', count: Object.keys(quotes).length, quotes };
    }
  } catch (err) {
    console.warn('Primary source (Tencent) failed, falling back to Sina:', err.message);
  }

  // 2. 尝试新浪
  try {
    const quotes = await fetchSina(parsedList);
    if (Object.keys(quotes).length > 0) {
      return { status: 'ok', source: 'sina', count: Object.keys(quotes).length, quotes };
    }
  } catch (err) {
    console.warn('Fallback 1 (Sina) failed, falling back to Xueqiu:', err.message);
  }

  // 3. 尝试雪球 (自动 Token)
  try {
    const quotes = await fetchXueqiu(parsedList);
    if (Object.keys(quotes).length > 0) {
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

  const headers = {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=5, s-maxage=5, stale-while-revalidate=15',
    'x-content-type-options': 'nosniff',
    'access-control-allow-origin': '*',
  };

  try {
    const data = await fetchQuote(symbols, exchange);
    return new Response(JSON.stringify(data), {
      status: data.status === 'ok' ? 200 : 400,
      headers,
    });
  } catch (err) {
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
