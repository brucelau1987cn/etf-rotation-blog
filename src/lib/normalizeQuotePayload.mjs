const legacyPayload = (payload) => payload?.ok === true && Array.isArray(payload.items);

const bareUsSymbol = (quote, key) => {
  const secCode = String(quote?.sec_code || '');
  if (secCode.startsWith('us') && secCode.length > 2) return secCode.slice(2).toUpperCase();
  return String(quote?.symbol || key || '').split('.')[0].replace(/^us/i, '').toUpperCase();
};

export function normalizeQuotePayload(payload) {
  if (legacyPayload(payload)) return payload;
  if (payload?.status !== 'ok' || !payload.quotes || typeof payload.quotes !== 'object') {
    return { ok: false, count: 0, items: [] };
  }

  const items = Object.entries(payload.quotes).map(([key, quote]) => ({
    symbol: bareUsSymbol(quote, key),
    price: quote?.price,
    low: quote?.low,
    change_pct: quote?.change_percent,
    change_percent: quote?.change_percent,
    quote_time: quote?.quote_time,
    status: quote?.status || 'ok',
  }));
  const generatedAt = items.map((item) => item.quote_time).filter(Boolean).sort().at(-1);

  return {
    ok: true,
    count: items.length,
    generated_at: generatedAt,
    items,
  };
}
