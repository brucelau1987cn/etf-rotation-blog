// GET /api/public/v1/rolling-instruments?market=a|futures|hk|us
// Public enabled instruments for rolling boards.
import { listRollingInstruments, normalizeMarket } from '../../../_lib/rolling-instruments.js';

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'GET') {
    return Response.json({ ok: false, error: 'method not allowed' }, { status: 405 });
  }
  if (!env.DB) {
    return Response.json({ ok: false, error: 'DB binding missing' }, { status: 500 });
  }
  try {
    const url = new URL(request.url);
    const market = normalizeMarket(url.searchParams.get('market') || '');
    const items = await listRollingInstruments(env.DB, {
      market: market || null,
      enabledOnly: true,
    });
    return Response.json(
      {
        ok: true,
        market: market || 'all',
        count: items.length,
        items: items.map(({ market: m, symbol, name, exchange, start_date, quote_symbol, sort_order }) => ({
          market: m,
          symbol,
          name,
          exchange,
          start_date,
          quote_symbol,
          sort_order,
        })),
      },
      { headers: { 'Cache-Control': 'public, max-age=15' } },
    );
  } catch (err) {
    return Response.json({ ok: false, error: String(err?.message || err) }, { status: 500 });
  }
}
