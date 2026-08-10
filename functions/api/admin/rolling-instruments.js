// /api/admin/rolling-instruments
// GET  → list (?market=)
// POST → create/update
// POST ?action=delete body:{market,symbol}
// POST ?action=toggle body:{market,symbol,enabled}
// POST ?action=set-start body:{market,symbol,start_date,clear_signals?}
//        改起始日期后默认同步清空该标的 D1 信号（重新启动观察）
import { isAdmin } from '../../_lib/subscription-auth.js';
import {
  clearRollingSignalsForSymbol,
  ensureRollingInstrumentsTable,
  listRollingInstruments,
  normalizeMarket,
  normalizeRollingInstrument,
  seedRollingInstrumentsIfEmpty,
} from '../../_lib/rolling-instruments.js';

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
    await seedRollingInstrumentsIfEmpty(env.DB);

    if (request.method === 'GET') {
      const market = normalizeMarket(url.searchParams.get('market') || '');
      const items = await listRollingInstruments(env.DB, {
        market: market || null,
        enabledOnly: false,
      });
      return Response.json({ ok: true, market: market || 'all', count: items.length, items });
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
      const market = normalizeMarket(body.market);
      const symbol = String(body.symbol || '').trim().toUpperCase();
      if (!market || !symbol) return Response.json({ ok: false, error: '缺少 market/symbol' }, { status: 400 });
      await env.DB.prepare('DELETE FROM rolling_instruments WHERE market = ? AND symbol = ?')
        .bind(market, symbol).run();
      // keep historical D1 signals unless explicitly asked
      if (body.clear_signals) {
        await clearRollingSignalsForSymbol(env.DB, symbol);
      }
      const items = await listRollingInstruments(env.DB, { market, enabledOnly: false });
      return Response.json({ ok: true, deleted: { market, symbol }, count: items.length, items });
    }

    if (action === 'toggle') {
      const market = normalizeMarket(body.market);
      const symbol = String(body.symbol || '').trim().toUpperCase();
      const enabled = body.enabled === 0 || body.enabled === false || body.enabled === '0' ? 0 : 1;
      if (!market || !symbol) return Response.json({ ok: false, error: '缺少 market/symbol' }, { status: 400 });
      const result = await env.DB.prepare(
        `UPDATE rolling_instruments SET enabled = ?, updated_at = datetime('now') WHERE market = ? AND symbol = ?`,
      ).bind(enabled, market, symbol).run();
      if (!result.meta?.changes) {
        return Response.json({ ok: false, error: `未找到 ${market}/${symbol}` }, { status: 404 });
      }
      const items = await listRollingInstruments(env.DB, { market, enabledOnly: false });
      return Response.json({ ok: true, market, symbol, enabled: enabled === 1, items });
    }

    if (action === 'set-start') {
      const market = normalizeMarket(body.market);
      let symbol = String(body.symbol || '').trim().toUpperCase();
      const start_date = String(body.start_date || '').trim();
      if (!market || !symbol) return Response.json({ ok: false, error: '缺少 market/symbol' }, { status: 400 });
      if (!/^\d{4}-\d{2}-\d{2}$/.test(start_date)) {
        return Response.json({ ok: false, error: 'start_date 须为 YYYY-MM-DD' }, { status: 400 });
      }
      if (market === 'hk' && /^\d{4}$/.test(symbol)) symbol = symbol.padStart(5, '0');

      const clearSignals = body.clear_signals === false || body.clear_signals === 0 || body.clear_signals === '0'
        ? false
        : true; // default true: 改起始 = 重新启动

      const result = await env.DB.prepare(`
        UPDATE rolling_instruments
        SET start_date = ?, reset_at = datetime('now'), updated_at = datetime('now')
        WHERE market = ? AND symbol = ?
      `).bind(start_date, market, symbol).run();
      if (!result.meta?.changes) {
        return Response.json({ ok: false, error: `未找到 ${market}/${symbol}` }, { status: 404 });
      }

      let cleared = { deleted: 0 };
      if (clearSignals) {
        cleared = await clearRollingSignalsForSymbol(env.DB, symbol);
      }

      const items = await listRollingInstruments(env.DB, { market, enabledOnly: false });
      return Response.json({
        ok: true,
        market,
        symbol,
        start_date,
        cleared_signals: cleared.deleted,
        items,
      });
    }

    // create / upsert
    const parsed = normalizeRollingInstrument(body, { forCreate: true });
    if (parsed.error) {
      return Response.json({ ok: false, error: parsed.error }, { status: 400 });
    }
    const item = parsed.item;
    await ensureRollingInstrumentsTable(env.DB);
    await env.DB.prepare(`
      INSERT INTO rolling_instruments
        (market, symbol, name, exchange, start_date, quote_symbol, sort_order, enabled, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
      ON CONFLICT(market, symbol) DO UPDATE SET
        name = excluded.name,
        exchange = excluded.exchange,
        start_date = excluded.start_date,
        quote_symbol = excluded.quote_symbol,
        sort_order = excluded.sort_order,
        enabled = excluded.enabled,
        updated_at = datetime('now')
    `).bind(
      item.market,
      item.symbol,
      item.name,
      item.exchange,
      item.start_date,
      item.quote_symbol,
      item.sort_order,
      item.enabled,
    ).run();

    if (body.clear_signals) {
      await clearRollingSignalsForSymbol(env.DB, item.symbol);
    }

    const items = await listRollingInstruments(env.DB, { market: item.market, enabledOnly: false });
    return Response.json({ ok: true, item, count: items.length, items });
  } catch (err) {
    return Response.json({ ok: false, error: String(err?.message || err) }, { status: 500 });
  }
}
