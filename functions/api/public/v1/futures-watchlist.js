// GET /api/public/v1/futures-watchlist
// Public read of enabled futures compass instruments.
import { listFuturesWatchlist } from '../../../_lib/futures-watchlist.js';

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== 'GET') {
    return Response.json({ ok: false, error: 'method not allowed' }, { status: 405 });
  }
  if (!env.DB) {
    return Response.json({ ok: false, error: 'DB binding missing' }, { status: 500 });
  }
  try {
    const items = await listFuturesWatchlist(env.DB, { enabledOnly: true });
    return Response.json(
      {
        ok: true,
        count: items.length,
        items: items.map(({ code, continuous, name, exchange, unit, tick, edge_symbol, sort_order }) => ({
          code, continuous, name, exchange, unit, tick, edge_symbol, sort_order,
        })),
      },
      {
        headers: {
          'Cache-Control': 'public, max-age=30',
        },
      },
    );
  } catch (err) {
    return Response.json({ ok: false, error: String(err?.message || err) }, { status: 500 });
  }
}
