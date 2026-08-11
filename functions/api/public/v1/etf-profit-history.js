/**
 * ETF 获利比历史（GLD/SLV 日/周/月筹码获利，THS 自算口径）
 *  GET /api/public/v1/etf-profit-history?asset=gold&limit=30  历史查询
 *  POST /api/public/v1/etf-profit-history                    追加记录（Bearer LOW_CHIP_SYNC_TOKEN）
 *    body: { records: [{ trade_date, asset, day_profit, week_profit, month_profit, price }] }
 */
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' },
});

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const method = request.method;

  const ensureTable = async () => {
    await env.DB.prepare(`CREATE TABLE IF NOT EXISTS etf_profit_history (
      trade_date TEXT NOT NULL,
      asset TEXT NOT NULL,
      day_profit REAL,
      week_profit REAL,
      month_profit REAL,
      price REAL,
      source TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (trade_date, asset)
    )`).run();
  };

  if (method === 'GET') {
    await ensureTable();
    const asset = url.searchParams.get('asset') || '';
    const limit = Math.min(Number(url.searchParams.get('limit') || 60), 365);
    let rows;
    if (asset) {
      rows = await env.DB.prepare(
        'SELECT trade_date, asset, day_profit, week_profit, month_profit, price, source, created_at FROM etf_profit_history WHERE asset = ? ORDER BY trade_date DESC LIMIT ?'
      ).bind(asset, limit).all();
    } else {
      rows = await env.DB.prepare(
        'SELECT trade_date, asset, day_profit, week_profit, month_profit, price, source, created_at FROM etf_profit_history ORDER BY trade_date DESC LIMIT ?'
      ).bind(limit).all();
    }
    return json({ ok: true, count: rows.results.length, records: rows.results });
  }

  if (method === 'POST') {
    const expected = String(env.LOW_CHIP_SYNC_TOKEN || '').trim();
    const auth = String(request.headers.get('authorization') || '');
    if (!expected || auth !== `Bearer ${expected}`) {
      return json({ error: 'unauthorized' }, 401);
    }
    let body;
    try { body = await request.json(); } catch {
      return json({ ok: false, error: 'invalid JSON' }, 400);
    }
    const records = body?.records;
    if (!Array.isArray(records) || records.length === 0) {
      return json({ ok: false, error: 'records array required' }, 400);
    }
    await ensureTable();
    let inserted = 0;
    for (const r of records) {
      if (!r.trade_date || !r.asset) continue;
      const result = await env.DB.prepare(`
        INSERT OR REPLACE INTO etf_profit_history
          (trade_date, asset, day_profit, week_profit, month_profit, price, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `).bind(
        r.trade_date, r.asset,
        r.day_profit ?? null, r.week_profit ?? null, r.month_profit ?? null,
        r.price ?? null, r.source || 'ths-kline'
      ).run();
      const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
      if (changes > 0) inserted++;
    }
    return json({ ok: true, inserted, total: records.length });
  }

  return json({ ok: false, error: 'method not allowed' }, 405);
}
