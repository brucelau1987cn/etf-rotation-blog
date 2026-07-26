const MARKETS = new Set(['CN_A', 'HK', 'US']);

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=300, s-maxage=3600',
    'x-content-type-options': 'nosniff',
  },
});

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const market = String(url.searchParams.get('market') || 'CN_A').toUpperCase();
  if (!MARKETS.has(market)) return json({ error: 'unsupported market' }, 400);
  const from = String(url.searchParams.get('from') || new Date().toISOString().slice(0, 10));
  const limit = Math.min(Math.max(Number(url.searchParams.get('limit')) || 14, 1), 366);
  const { results } = await env.DB.prepare(`SELECT market, trade_date, is_open, open_at, break_start_at, break_end_at,
    close_at, session_type, note, source, updated_at
    FROM market_calendar WHERE market = ? AND trade_date >= ? ORDER BY trade_date LIMIT ?`)
    .bind(market, from, limit).all();
  return json({ market, from, count: results.length, sessions: results });
}
