/**
 * Canonical quote payload adapter for Edge Quote API.
 * Accepts:
 *   - new contract: { status: 'ok', quotes: { [key]: { price, change_percent, ... } } }
 *   - legacy contract: { ok: true, items: [...] }
 * Always returns: { ok, count, generated_at?, items: [{ symbol, code, price, ... }] }
 */

const legacyPayload = (payload) => payload?.ok === true && Array.isArray(payload.items);

export function bareSymbol(quote, key = '') {
  const secCode = String(quote?.sec_code || '');
  if (secCode.startsWith('us') && secCode.length > 2) {
    return secCode.slice(2).toUpperCase();
  }
  if (secCode.startsWith('hk') && secCode.length > 2) {
    return secCode.slice(2);
  }
  if (/^(sh|sz|bj)/i.test(secCode) && secCode.length > 2) {
    return secCode.slice(2);
  }

  let symbol = String(quote?.symbol || key || '');
  symbol = symbol.split('.')[0];
  symbol = symbol.replace(/^(us|hk|sh|sz|bj)/i, '');
  // US tickers stay upper-case; pure digits keep digits.
  return /^[A-Za-z]/.test(symbol) ? symbol.toUpperCase() : symbol;
}

export function normalizeQuotePayload(payload) {
  if (legacyPayload(payload)) {
    const items = payload.items.map((item) => {
      const symbol = bareSymbol(item, item?.symbol || item?.code || '');
      return {
        ...item,
        symbol,
        code: item?.code || symbol,
        change_pct: item?.change_pct ?? item?.change_percent,
        change_percent: item?.change_percent ?? item?.change_pct,
        status: item?.status || 'ok',
      };
    });
    return {
      ok: true,
      count: items.length,
      generated_at: payload.generated_at,
      source: payload.source,
      items,
    };
  }

  if (payload?.status !== 'ok' || !payload.quotes || typeof payload.quotes !== 'object') {
    return { ok: false, count: 0, items: [] };
  }

  const items = Object.entries(payload.quotes).map(([key, quote]) => {
    const symbol = bareSymbol(quote, key);
    return {
      symbol,
      code: symbol,
      price: quote?.price,
      low: quote?.low,
      high: quote?.high,
      open: quote?.open,
      prev_close: quote?.prev_close,
      change_pct: quote?.change_percent,
      change_percent: quote?.change_percent,
      quote_time: quote?.quote_time,
      name: quote?.name,
      market: quote?.market,
      status: quote?.status || 'ok',
      source: payload.source || quote?.source,
    };
  });

  const generatedAt = items.map((item) => item.quote_time).filter(Boolean).sort().at(-1);
  return {
    ok: true,
    count: items.length,
    generated_at: generatedAt,
    source: payload.source,
    items,
  };
}

export function findQuoteItem(payloadOrNormalized, code) {
  const normalized = payloadOrNormalized?.items
    ? payloadOrNormalized
    : normalizeQuotePayload(payloadOrNormalized);
  if (!normalized?.ok || !Array.isArray(normalized.items)) return null;
  const target = String(code || '').split('.')[0].replace(/^(us|hk|sh|sz|bj)/i, '');
  const upper = target.toUpperCase();
  return (
    normalized.items.find((item) => String(item.symbol) === target || String(item.code) === target)
    || normalized.items.find((item) => String(item.symbol).toUpperCase() === upper || String(item.code).toUpperCase() === upper)
    || null
  );
}

/** Build Edge API symbols= param for pure A-share 6-digit codes. */
export function aShareSymbolsParam(codes) {
  return [...new Set((codes || []).filter(Boolean).map(String))].map((code) => {
    const bare = code.split('.')[0];
    if (bare.length !== 6 || !/^\d{6}$/.test(bare)) return bare;
    const isSZ = bare.startsWith('159') || bare.startsWith('300') || bare.startsWith('00') || bare.startsWith('399');
    return `${bare}.${isSZ ? 'SZ' : 'SH'}`;
  }).join(',');
}

export function quoteMapByCode(normalized) {
  const map = new Map();
  for (const item of normalized?.items || []) {
    map.set(String(item.code || item.symbol), item);
    map.set(String(item.symbol || item.code).toUpperCase(), item);
  }
  return map;
}
