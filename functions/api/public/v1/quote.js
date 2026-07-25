/**
 * Cloudflare Worker / Pages Function - Edge Stock & Futures Quote API
 * 单个/批量代理解析 A股、港股、美股及期货极速行情接口，带 Cloudflare Edge 5s 缓存
 */

export function parseSymbol(rawSymbol, defaultExchange = 'SSE') {
  const s = rawSymbol.trim();
  if (!s) return null;

  // 1. 如果已经包含显式市场前缀/后缀
  if (s.includes('.')) {
    const parts = s.split('.');
    const code = parts[0];
    const ex = parts[1].toUpperCase();

    if (ex === 'HK') return { secCode: `hk${code.padStart(5, '0')}`, displayCode: code, type: 'hk' };
    if (ex === 'US') return { secCode: `us${code}`, displayCode: code, type: 'us' };
    if (ex === 'SZ' || ex === 'SZSE') return { secCode: `sz${code}`, displayCode: code, type: 'a' };
    if (ex === 'SH' || ex === 'SSE') return { secCode: `sh${code}`, displayCode: code, type: 'a' };
    if (ex === 'BJ') return { secCode: `bj${code}`, displayCode: code, type: 'a' };
  }

  // 前缀判断
  if (s.startsWith('hk')) return { secCode: s, displayCode: s.slice(2), type: 'hk' };
  if (s.startsWith('us')) return { secCode: s, displayCode: s.slice(2), type: 'us' };
  if (s.startsWith('hf_') || s.startsWith('nf_')) return { secCode: s, displayCode: s, type: 'futures' };
  if (s.startsWith('sh') || s.startsWith('sz') || s.startsWith('bj')) return { secCode: s, displayCode: s.slice(2), type: 'a' };

  // 纯代码自动推断
  // 5位纯数字 -> 港股
  if (/^\d{5}$/.test(s)) return { secCode: `hk${s}`, displayCode: s, type: 'hk' };
  // 纯字母且1-5位 -> 美股 (如 AAPL, TSLA, NVDA, BABA)
  if (/^[A-Za-z]{1,5}$/.test(s)) return { secCode: `us${s}`, displayCode: s.toUpperCase(), type: 'us' };

  // A股代码推断 (6位数字)
  if (/^\d{6}$/.test(s)) {
    const isSZ = defaultExchange === 'SZSE' || s.startsWith('159') || s.startsWith('300') || s.startsWith('00') || s.startsWith('399');
    return { secCode: isSZ ? `sz${s}` : `sh${s}`, displayCode: s, type: 'a' };
  }

  return { secCode: s, displayCode: s, type: 'unknown' };
}

export async function fetchQuote(symbolsStr, defaultExchange = 'SSE') {
  const rawItems = (symbolsStr || '600021').split(',').map(s => s.trim()).filter(Boolean).slice(0, 50);
  if (rawItems.length === 0) return { status: 'error', message: 'no symbols provided' };

  const parsedList = rawItems.map(item => parseSymbol(item, defaultExchange)).filter(Boolean);
  const secCodes = parsedList.map(p => p.secCode);

  const upstreamUrl = `https://qt.gtimg.cn/q=${secCodes.join(',')}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);

  const upstreamRes = await fetch(upstreamUrl, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)',
      'Referer': 'https://finance.qq.com/',
    },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));

  if (!upstreamRes.ok) throw new Error(`upstream HTTP ${upstreamRes.status}`);

  const buffer = await upstreamRes.arrayBuffer();
  const decoder = new TextDecoder('gbk');
  const text = decoder.decode(buffer);

  const quotes = {};
  const statements = text.split(';');

  for (const stmt of statements) {
    // 1. 解析外盘/内盘期货 (如 v_hf_CL="90.72,-1.59,90.47,90.48,92.83,87.68,04:59:58,92.19,92.55,0,1,6,2026-07-25,纽约原油")
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
          status: 'ok',
        };
        continue;
      }
    }

    // 2. 解析股票/ETF (A股 sh/sz/bj、港股 hk、美股 us)
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
    if (rawTime) {
      if (rawTime.length === 14) {
        quoteTime = `${rawTime.slice(0, 4)}-${rawTime.slice(4, 6)}-${rawTime.slice(6, 8)}T${rawTime.slice(8, 10)}:${rawTime.slice(10, 12)}:${rawTime.slice(12, 14)}+08:00`;
      } else if (rawTime.includes('/') || rawTime.includes('-')) {
        quoteTime = rawTime;
      }
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
      status: 'ok',
    };
  }

  return {
    status: 'ok',
    count: Object.keys(quotes).length,
    quotes,
  };
}

export default {
  async fetch(request) {
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
  },
};
