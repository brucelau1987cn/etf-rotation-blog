// GET  /api/public/v1/buy-stock
//   → positions (open + closed) plus the fee parameters the page needs.
//
// POST /api/public/v1/buy-stock
//   → admin only: create / update a position.
// POST /api/public/v1/buy-stock?action=delete  body:{code}
//   → admin only: hard delete a position.
//
// Auth: reads require a subscriber or admin cookie; writes require admin.
// The holdings are private, so the page and this endpoint both require a
// logged-in identity.
//
// Live prices are NOT fetched here: the page reuses the public
// /api/public/v1/quote endpoint, which every other board on the site already
// uses. Keeping quotes out of this endpoint means the fee maths lives in one
// place (public/js/buy-stock-fees.js) instead of being duplicated in a
// Pages Function.

import { isSubscribed, isAdmin } from '../../../_lib/subscription-auth.js';
import { ensureBuyStockTable, listPositions, normalizePosition } from '../../../_lib/buy-stock.js';

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

function serialize(row) {
  return {
    code: row.code,
    name: row.name || row.code,
    market: row.market || '',
    buy_price: Number(row.buy_price),
    shares: Number(row.shares),
    buy_time: row.buy_time || '',
    buy_date: row.buy_date || (row.buy_time || '').slice(0, 10),
    note: row.note || '',
    sold_at: row.sold_at || null,
    sell_price: row.sell_price === null || row.sell_price === undefined ? null : Number(row.sell_price),
    sell_time: row.sell_time || null,
  };
}

async function respondWithPositions(db) {
  const rows = await listPositions(db, { includeClosed: true });
  const positions = rows.map(serialize);
  return json({
    ok: true,
    positions,
    open_count: positions.filter((item) => !item.sold_at).length,
    closed_count: positions.filter((item) => item.sold_at).length,
    generated_at: new Date().toISOString(),
  });
}

export async function onRequestGet(context) {
  const { request, env } = context;

  const subscribed = await isSubscribed(request, env);
  const admin = await isAdmin(request, env);
  if (!subscribed && !admin) {
    return json({ ok: false, error: '需要登录' }, 401);
  }
  if (!env.DB) {
    return json({ ok: false, error: 'DB binding missing' }, 500);
  }

  try {
    await ensureBuyStockTable(env.DB);
    return await respondWithPositions(env.DB);
  } catch (err) {
    return json({ ok: false, error: String(err?.message || err) }, 500);
  }
}

export async function onRequestPost(context) {
  const { request, env } = context;
  // request.url is a STRING on the Workers runtime, so parse it before
  // touching searchParams — reading .searchParams on the string throws.
  const url = new URL(request.url);

  if (!(await isAdmin(request, env))) {
    return json({ ok: false, error: '需要管理员权限' }, 401);
  }
  if (!env.DB) {
    return json({ ok: false, error: 'DB binding missing' }, 500);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: '无效请求' }, 400);
  }

  try {
    await ensureBuyStockTable(env.DB);
    const action = url.searchParams.get('action');

    if (action === 'delete') {
      const code = String(body.code || '').trim().toUpperCase();
      if (!/^\d{6}$/.test(code)) return json({ ok: false, error: '代码必须是6位数字' }, 400);
      await env.DB.prepare('DELETE FROM buy_stock_positions WHERE code = ?').bind(code).run();
      return respondWithPositions(env.DB);
    }

    const parsed = normalizePosition(body, { forCreate: true });
    if (parsed.error) return json({ ok: false, error: parsed.error }, 400);
    const position = parsed.position;

    await env.DB.prepare(`
      INSERT INTO buy_stock_positions
        (code, name, market, buy_price, shares, buy_time, buy_date, note, sold_at, sell_price, sell_time)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(code) DO UPDATE SET
        name = excluded.name,
        market = excluded.market,
        buy_price = excluded.buy_price,
        shares = excluded.shares,
        buy_time = excluded.buy_time,
        buy_date = excluded.buy_date,
        note = excluded.note,
        sold_at = excluded.sold_at,
        sell_price = excluded.sell_price,
        sell_time = excluded.sell_time,
        updated_at = datetime('now')
    `).bind(
      position.code,
      position.name,
      position.market,
      position.buy_price,
      position.shares,
      position.buy_time,
      position.buy_date,
      position.note,
      position.sold_at,
      position.sell_price,
      position.sell_time,
    ).run();

    return respondWithPositions(env.DB);
  } catch (err) {
    return json({ ok: false, error: String(err?.message || err) }, 500);
  }
}
