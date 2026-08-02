/**
 * Jin10 ETF holdings report proxy (黄金ETF attr_id=1 / 白银ETF attr_id=2).
 *
 * Hides the upstream x-token from the browser. Aggregates weekly
 * inc/dec trust and value; optional weeks limit (default 12).
 */
const UPSTREAM = 'https://mp-api.jin10.com/api/etf-reports/view';

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=1800' },
});

const ATTRS = { 1: 'gold', 2: 'silver' };

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const attrId = Number(url.searchParams.get('attr_id')) || 2;
  if (!ATTRS[attrId]) return json({ error: 'attr_id must be 1 (gold) or 2 (silver)', source: 'jin10-etf-reports' }, 400);

  const weeks = Math.max(1, Math.min(52, Number(url.searchParams.get('weeks')) || 12));
  const now = new Date();
  // Upstream buckets weekly (Fridays); pull ~2 weeks per requested week to be safe.
  const dateStart = new Date(now);
  dateStart.setDate(dateStart.getDate() - (weeks * 14));
  const dateEnd = new Date(now);
  dateEnd.setDate(dateEnd.getDate() + 7);
  const iso = (d) => d.toISOString().slice(0, 10);
  const qs = new URLSearchParams({
    attr_id: String(attrId),
    date_start: iso(dateStart),
    date: iso(dateEnd),
    unit: 'week',
  });

  let upstream;
  try {
    const token = env.JIN10_X_TOKEN || '5c5b9d34-0899-441c-9cb6-60f58a0ec731';
    const resp = await fetch(`${UPSTREAM}?${qs}`, {
      headers: {
        'x-token': token,
        'x-version': '1.0',
        'x-app-id': env.JIN10_X_APP_ID || 'fiXF2nOnDycGutVA',
        'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Html5Plus/1.0 (Immersed/59) uni-app',
        'accept': '*/*',
        'accept-language': 'zh-CN,zh-Hans;q=0.9',
      },
    });
    if (!resp.ok) return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-etf-reports' }, 502);
    upstream = await resp.json();
  } catch (error) {
    return json({ error: 'upstream fetch failed', detail: error.message, source: 'jin10-etf-reports' }, 502);
  }

  const rows = (upstream?.data || []).slice(-weeks);
  const netTrust = rows.reduce((sum, r) => sum + (r.inc_trust || 0) - (r.dec_trust || 0), 0);
  const incValueTotal = rows.reduce((sum, r) => sum + (r.inc_value || 0), 0);
  const decValueTotal = rows.reduce((sum, r) => sum + (r.dec_value || 0), 0);
  const latest = rows.at(-1) || null;
  return json({
    status: 'ok',
    source: 'jin10-etf-reports',
    asset: ATTRS[attrId],
    attr_id: attrId,
    unit: 't',
    weeks,
    net_trust: Number(netTrust.toFixed(3)),
    inc_value_total: incValueTotal,
    dec_value_total: decValueTotal,
    latest,
    rows,
  });
}