/**
 * Jin10 ETF holdings report proxy (黄金ETF attr_id=1 / 白银ETF attr_id=2).
 *
 * Hides the upstream x-token from the browser.
 *
 * Two upstream endpoints:
 *  - /api/etf-reports        → daily snapshots {trust, change, value, reported_on}
 *  - /api/etf-reports/view   → weekly/monthly aggregates {inc_trust, dec_trust, ...}
 *
 * Daily mode (default): filters snapshots by trust magnitude to separate
 * gold (~1000t) from silver (~15000t), returns newest-first.
 */
const UPSTREAM = 'https://mp-api.jin10.com/api/etf-reports';
const UPSTREAM_VIEW = 'https://mp-api.jin10.com/api/etf-reports/view';

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=1800' },
});

const ATTRS = { 1: 'gold', 2: 'silver' };

// Gold ETF holds ~1000t, silver ~15000t; use midpoint to separate.
const trustBoundary = (attrId) => (attrId === 1 ? 5000 : 5000);

const upstreamHeaders = (env) => ({
  'x-token': env.JIN10_X_TOKEN || '5c5b9d34-0899-441c-9cb6-60f58a0ec731',
  'x-version': '1.0',
  'x-app-id': env.JIN10_X_APP_ID || 'fiXF2nOnDycGutVA',
  'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Html5Plus/1.0 (Immersed/59) uni-app',
  'accept': '*/*',
  'accept-language': 'zh-CN,zh-Hans;q=0.9',
});

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const attrId = Number(url.searchParams.get('attr_id')) || 2;
  if (!ATTRS[attrId]) return json({ error: 'attr_id must be 1 (gold) or 2 (silver)', source: 'jin10-etf-reports' }, 400);

  const unit = url.searchParams.get('unit') === 'week' ? 'week' : 'day';
  const limit = Math.max(1, Math.min(90, Number(url.searchParams.get('limit')) || 15));
  const now = new Date();
  const dateStart = new Date(now);
  dateStart.setDate(dateStart.getDate() - (unit === 'week' ? limit * 14 : limit * 3));
  const dateEnd = new Date(now);
  dateEnd.setDate(dateEnd.getDate() + 7);
  const iso = (d) => d.toISOString().slice(0, 10);

  try {
    if (unit === 'day') {
      // Daily snapshots: both assets mixed in one stream; filter by trust magnitude.
      const qs = new URLSearchParams({ date_start: iso(dateStart), date: iso(dateEnd) });
      const resp = await fetch(`${UPSTREAM}?${qs}`, { headers: upstreamHeaders(env) });
      if (!resp.ok) return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-etf-reports' }, 502);
      const upstream = await resp.json();
      const boundary = 5000;
      const rows = (upstream?.data || [])
        .filter((r) => (attrId === 1 ? r.trust < boundary : r.trust >= boundary))
        .slice(0, limit)
        .map((r) => ({
          reported_on: r.reported_on,
          trust: r.trust,
          change: r.change,
          value: r.value,
          updated_at: r.updated_at,
        }));
      const netChange = rows.reduce((sum, r) => sum + (r.change || 0), 0);
      return json({
        status: 'ok',
        source: 'jin10-etf-reports',
        asset: ATTRS[attrId],
        attr_id: attrId,
        unit: 'day',
        limit,
        net_change: Number(netChange.toFixed(3)),
        latest: rows[0] || null,
        rows,
      });
    }

    // Weekly aggregates from /view
    const qs = new URLSearchParams({ attr_id: String(attrId), date_start: iso(dateStart), date: iso(dateEnd), unit: 'week' });
    const resp = await fetch(`${UPSTREAM_VIEW}?${qs}`, { headers: upstreamHeaders(env) });
    if (!resp.ok) return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-etf-reports' }, 502);
    const upstream = await resp.json();
    const rows = (upstream?.data || []).slice(0, limit);
    const netTrust = rows.reduce((sum, r) => sum + (r.inc_trust || 0) - (r.dec_trust || 0), 0);
    const incValueTotal = rows.reduce((sum, r) => sum + (r.inc_value || 0), 0);
    const decValueTotal = rows.reduce((sum, r) => sum + (r.dec_value || 0), 0);
    return json({
      status: 'ok',
      source: 'jin10-etf-reports',
      asset: ATTRS[attrId],
      attr_id: attrId,
      unit: 'week',
      limit,
      net_trust: Number(netTrust.toFixed(3)),
      inc_value_total: incValueTotal,
      dec_value_total: decValueTotal,
      latest: rows[0] || null,
      rows,
    });
  } catch (error) {
    return json({ error: 'upstream fetch failed', detail: error.message, source: 'jin10-etf-reports' }, 502);
  }
}