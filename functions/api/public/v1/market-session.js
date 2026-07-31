const MARKET_TZ = { CN_A: 'Asia/Shanghai', HK: 'Asia/Hong_Kong', US: 'America/New_York' };

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=5, s-maxage=15',
    'x-content-type-options': 'nosniff',
  },
});

const localDate = (tz) => new Intl.DateTimeFormat('en-CA', {
  timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date());

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const market = String(url.searchParams.get('market') || 'CN_A').toUpperCase();
  const tz = MARKET_TZ[market];
  if (!tz) return json({ error: 'unsupported market' }, 400);
  const today = localDate(tz);
  const row = await env.DB.prepare(`SELECT market, trade_date, is_open, open_at, break_start_at, break_end_at,
    close_at, session_type, note, source, updated_at
    FROM market_calendar WHERE market = ? AND trade_date = ?`).bind(market, today).first();
  const next = await env.DB.prepare(`SELECT trade_date, open_at FROM market_calendar
    WHERE market = ? AND trade_date > ? AND is_open = 1 ORDER BY trade_date LIMIT 1`).bind(market, today).first();
  return json({ market, timezone: tz, today, session: row || null, next_open_session: next || null });
}
