// /api/admin/futures-watchlist
// GET  → list all (enabled + disabled)
// POST → create/update one instrument
// POST ?action=delete body:{code} → hard delete
// POST ?action=toggle body:{code,enabled} → enable/disable
import { isAdmin } from '../../_lib/subscription-auth.js';
import {
  ensureFuturesWatchlistTable,
  listFuturesWatchlist,
  normalizeWatchlistItem,
  seedFuturesWatchlistIfEmpty,
} from '../../_lib/futures-watchlist.js';

export async function onRequest(context) {
  const { request, env } = context;
  if (!(await isAdmin(request, env))) {
    return Response.json({ ok: false, error: '未登录或会话过期' }, { status: 401 });
  }
  if (!env.DB) {
    return Response.json({ ok: false, error: 'DB binding missing' }, { status: 500 });
  }

  const url = new URL(request.url);
  const action = url.searchParams.get('action');

  try {
    await seedFuturesWatchlistIfEmpty(env.DB);

    if (request.method === 'GET') {
      const items = await listFuturesWatchlist(env.DB, { enabledOnly: false });
      return Response.json({ ok: true, count: items.length, items });
    }

    if (request.method !== 'POST') {
      return Response.json({ ok: false, error: 'method not allowed' }, { status: 405 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }

    if (action === 'delete') {
      const code = String(body.code || '').trim().toUpperCase();
      if (!code) return Response.json({ ok: false, error: '缺少 code' }, { status: 400 });
      await env.DB.prepare('DELETE FROM futures_watchlist WHERE code = ?').bind(code).run();
      const items = await listFuturesWatchlist(env.DB, { enabledOnly: false });
      return Response.json({ ok: true, deleted: code, count: items.length, items });
    }

    if (action === 'toggle') {
      const code = String(body.code || '').trim().toUpperCase();
      const enabled = body.enabled === 0 || body.enabled === false || body.enabled === '0' ? 0 : 1;
      if (!code) return Response.json({ ok: false, error: '缺少 code' }, { status: 400 });
      const result = await env.DB.prepare(
        `UPDATE futures_watchlist SET enabled = ?, updated_at = datetime('now') WHERE code = ?`,
      ).bind(enabled, code).run();
      if (!result.meta?.changes) {
        return Response.json({ ok: false, error: `未找到 ${code}` }, { status: 404 });
      }
      const items = await listFuturesWatchlist(env.DB, { enabledOnly: false });
      return Response.json({ ok: true, code, enabled: enabled === 1, items });
    }

    // create / upsert
    const parsed = normalizeWatchlistItem(body, { forCreate: true });
    if (parsed.error) {
      return Response.json({ ok: false, error: parsed.error }, { status: 400 });
    }
    const item = parsed.item;
    await ensureFuturesWatchlistTable(env.DB);
    await env.DB.prepare(`
      INSERT INTO futures_watchlist
        (code, continuous, name, exchange, unit, tick, edge_symbol, sort_order, enabled, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
      ON CONFLICT(code) DO UPDATE SET
        continuous = excluded.continuous,
        name = excluded.name,
        exchange = excluded.exchange,
        unit = excluded.unit,
        tick = excluded.tick,
        edge_symbol = excluded.edge_symbol,
        sort_order = excluded.sort_order,
        enabled = excluded.enabled,
        updated_at = datetime('now')
    `).bind(
      item.code,
      item.continuous,
      item.name,
      item.exchange,
      item.unit,
      item.tick,
      item.edge_symbol,
      item.sort_order,
      item.enabled,
    ).run();

    const items = await listFuturesWatchlist(env.DB, { enabledOnly: false });
    return Response.json({ ok: true, item, count: items.length, items });
  } catch (err) {
    return Response.json({ ok: false, error: String(err?.message || err) }, { status: 500 });
  }
}
