/**
 * GET /api/public/v1/quote?symbol=600021&exchange=SSE
 * 代理并解析腾讯股票极速行情接口，带 Cloudflare 5s 缓存与安全 CORS
 */

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const symbol = url.searchParams.get('symbol') || '600021';
  const exchange = (url.searchParams.get('exchange') || 'SSE').toUpperCase();
  const secCode = exchange === 'SZSE' ? `sz${symbol}` : `sh${symbol}`;

  const defaultHeaders = {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=5, s-maxage=5, stale-while-revalidate=15',
    'x-content-type-options': 'nosniff',
    'access-control-allow-origin': '*',
  };

  try {
    const upstreamUrl = `https://qt.gtimg.cn/q=${secCode}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);

    const upstreamRes = await fetch(upstreamUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)',
        'Referer': 'https://finance.qq.com/',
      },
      signal: controller.signal,
    }).finally(() => clearTimeout(timer));

    if (!upstreamRes.ok) {
      throw new Error(`upstream HTTP ${upstreamRes.status}`);
    }

    const buffer = await upstreamRes.arrayBuffer();
    const decoder = new TextDecoder('gbk');
    const text = decoder.decode(buffer);

    // 解析 v_sh600021="1~上海电力~600021~14.74~15.34~..."
    const match = text.match(/="([^"]+)"/);
    if (!match) {
      throw new Error('invalid quote payload format');
    }

    const parts = match[1].split('~');
    if (parts.length < 35) {
      throw new Error('insufficient quote fields');
    }

    const name = parts[1] || '上海电力';
    const code = parts[2] || symbol;
    const currentPrice = parseFloat(parts[3]) || 0;
    const prevClose = parseFloat(parts[4]) || 0;
    const openPrice = parseFloat(parts[5]) || 0;
    const volume = parseInt(parts[6], 10) || 0; // 手
    const changeAmount = parseFloat(parts[31]) || (currentPrice - prevClose);
    const changePercent = parseFloat(parts[32]) || 0;
    const highPrice = parseFloat(parts[33]) || 0;
    const lowPrice = parseFloat(parts[34]) || 0;
    const rawTime = parts[30] || ''; // YYYYMMDDHHMMSS

    let quoteTime = new Date().toISOString();
    if (rawTime && rawTime.length === 14) {
      const year = rawTime.slice(0, 4);
      const month = rawTime.slice(4, 6);
      const day = rawTime.slice(6, 8);
      const hour = rawTime.slice(8, 10);
      const minute = rawTime.slice(10, 12);
      const second = rawTime.slice(12, 14);
      quoteTime = `${year}-${month}-${day}T${hour}:${minute}:${second}+08:00`;
    }

    const quoteData = {
      symbol: code,
      exchange,
      name,
      price: currentPrice,
      prev_close: prevClose,
      open: openPrice,
      high: highPrice,
      low: lowPrice,
      change_amount: parseFloat(changeAmount.toFixed(2)),
      change_percent: parseFloat(changePercent.toFixed(2)),
      volume_hands: volume,
      quote_time: quoteTime,
      status: 'ok',
    };

    return new Response(JSON.stringify(quoteData), {
      status: 200,
      headers: defaultHeaders,
    });
  } catch (err) {
    const fallback = {
      symbol,
      exchange,
      name: '上海电力',
      price: null,
      change_percent: null,
      status: 'error',
      message: err.message,
    };
    return new Response(JSON.stringify(fallback), {
      status: 200,
      headers: defaultHeaders,
    });
  }
}
