import { persistJin10EtfHoldings, readJin10EtfHoldings } from '../../../_lib/jin10-etf-reports-d1.js';

/**
 * Jin10 ETF holdings report proxy (黄金ETF attr_id=1 / 白银ETF attr_id=2).
 *
 * Hides the upstream x-token from the browser. Persists daily snapshots
 * to D1 (jin10_etf_holdings) on every upstream fetch.
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

const upstreamHeaders = (env) => ({
  'x-token': env.JIN10_X_TOKEN || '5c5b9d34-0899-441c-9cb6-60f58a0ec731',
  'x-version': '1.0',
  'x-app-id': env.JIN10_X_APP_ID || 'fiXF2nOnDycGutVA',
  'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Html5Plus/1.0 (Immersed/59) uni-app',
  'accept': '*/*',
  'accept-language': 'zh-CN,zh-Hans;q=0.9',
});

const buildResponse = (attrId, unit, limit, rows) => {
  const netChange = rows.reduce((sum, r) => sum + (r.change || 0), 0);
  const isWeek = unit === 'week';
  return json({
    status: 'ok',
    source: 'jin10-etf-reports',
    asset: ATTRS[attrId],
    attr_id: attrId,
    unit,
    limit,
    ...(isWeek
      ? { net_trust: Number(netChange.toFixed(3)), inc_value_total: 0, dec_value_total: 0 }
      : { net_change: Number(netChange.toFixed(3)) }
    ),
    latest: rows[0] || null,
    rows,
  });
};

export async function onRequestGet({ request, env, waitUntil }) {
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
      // 1. Try D1 first
      let rows;
      const db = env?.DB;
      if (db?.prepare) {
        try {
          rows = await readJin10EtfHoldings(db, attrId, limit);
        } catch (_) { /* fall through to upstream */ }
      }

      // 2. If D1 has enough rows and newest is recent (≤2 days old), serve from D1.
      const newestDate = rows?.[0]?.reported_on;
      const newestAgeDays = newestDate ? (Date.now() - Date.parse(newestDate)) / 86400000 : Infinity;
      if (rows && rows.length >= limit && newestAgeDays <= 2) {
        return buildResponse(attrId, unit, limit, rows);
      }

      // 3. Fallback / refresh: fetch upstream
      const qs = new URLSearchParams({ date_start: iso(dateStart), date: iso(dateEnd) });
      const resp = await fetch(`${UPSTREAM}?${qs}`, { headers: upstreamHeaders(env) });
      if (!resp.ok) {
        if (rows && rows.length > 0) return buildResponse(attrId, unit, limit, rows);
        return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-etf-reports' }, 502);
      }
      const upstream = await resp.json();
      const boundary = 5000;
      rows = (upstream?.data || [])
        .filter((r) => (attrId === 1 ? r.trust < boundary : r.trust >= boundary))
        .slice(0, limit)
        .map((r) => ({
          reported_on: r.reported_on,
          trust: r.trust,
          change: r.change,
          value: r.value,
          updated_at: r.updated_at,
        }));

      // 4. Persist to D1 (async, non-blocking)
      if (db?.prepare && waitUntil) {
        try {
          waitUntil(persistJin10EtfHoldings(db, attrId, rows));
        } catch (_) { /* non-blocking */ }
      }

      return buildResponse(attrId, unit, limit, rows);
    }

    // Weekly aggregates from /view (no D1 persistence)
    const qs = new URLSearchParams({ attr_id: String(attrId), date_start: iso(dateStart), date: iso(dateEnd), unit: 'week' });
    const resp = await fetch(`${UPSTREAM_VIEW}?${qs}`, { headers: upstreamHeaders(env) });
    if (!resp.ok) return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-etf-reports' }, 502);
    const upstream = await resp.json();
    const rows = (upstream?.data || []).slice(0, limit);
    const netTrust = rows.reduce((sum, r) => sum + (r.inc_trust || 0) - (r.dec_trust || 0), 0);
    return json({
      status: 'ok', source: 'jin10-etf-reports', asset: ATTRS[attrId], attr_id: attrId,
      unit: 'week', limit, net_trust: Number(netTrust.toFixed(3)),
      inc_value_total: rows.reduce((s, r) => s + (r.inc_value || 0), 0),
      dec_value_total: rows.reduce((s, r) => s + (r.dec_value || 0), 0),
      latest: rows[0] || null, rows,
    });
  } catch (error) {
    return json({ error: 'upstream fetch failed', detail: error.message, source: 'jin10-etf-reports' }, 502);
  }
}