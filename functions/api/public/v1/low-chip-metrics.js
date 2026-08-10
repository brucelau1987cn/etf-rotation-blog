/**
 * POST /api/public/v1/low-chip-metrics
 * Write stock metrics (股东人数/主力控盘 etc.) to D1.
 * Auth: Bearer token via env.LOW_CHIP_SYNC_TOKEN
 * Body: { metrics: [{ trade_date, stock_code, stock_name, shareholder_count,
 *   shareholder_change_pct, main_force, main_force_label, concentration90,
 *   top10_float_ratio, price }] }
 *
 * GET /api/public/v1/low-chip-metrics?date=YYYY-MM-DD&code=002992
 * Query stock metrics.
 */

function json(data, status = 200, cache = 'no-store') {
  return new Response(JSON.stringify(data), {
    status, headers: { 'Content-Type': 'application/json', 'Cache-Control': cache },
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const method = request.method;

  // Ensure table exists
  const ensureTable = async () => {
    if (!env.DB) return;
    await env.DB.prepare(`
      CREATE TABLE IF NOT EXISTS stock_metrics (
        trade_date TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        shareholder_count REAL,
        shareholder_change_pct REAL,
        main_force REAL,
        main_force_label TEXT,
        concentration90 REAL,
        chip_focus TEXT,
        report_period TEXT,
        top10_float_ratio REAL,
        price REAL,
        announcement_date TEXT,
        week_profit REAL,
        month_profit REAL,
        quarter_profit REAL,
        change_percent REAL,
        industry TEXT,
        sector TEXT,
        financials TEXT,
        theme_concepts TEXT,
        quality_shareholder INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, stock_code)
      )
    `).run();
    // Backward-compatible column add
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN announcement_date TEXT').run(); } catch (e) {}
  };

  if (method === 'GET') {
    const tradeDate = url.searchParams.get('date');
    const code = url.searchParams.get('code');
    if (!tradeDate) return json({ ok: false, error: 'date parameter required' }, 400);
    await ensureTable();
    let results;
    if (code) {
      const r = await env.DB.prepare(
        'SELECT * FROM stock_metrics WHERE trade_date = ? AND stock_code = ?'
      ).bind(tradeDate, code).all();
      results = r.results || [];
    } else {
      const r = await env.DB.prepare(
        'SELECT * FROM stock_metrics WHERE trade_date = ? ORDER BY main_force DESC'
      ).bind(tradeDate).all();
      results = r.results || [];
    }
    return json({ ok: true, trade_date: tradeDate, count: results.length, results });
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
    const metrics = body?.metrics;
    if (!Array.isArray(metrics) || metrics.length === 0) {
      return json({ ok: false, error: 'metrics array required' }, 400);
    }

    await ensureTable();
    // Ensure new columns exist on pre-existing tables (fail silently if present)
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN chip_focus TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN report_period TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN week_profit REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN month_profit REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN quarter_profit REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN change_percent REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN industry TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN sector TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN financials TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN theme_concepts TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN quality_shareholder INTEGER').run(); } catch (e) {}
    let inserted = 0;
    for (const m of metrics) {
      if (!m.trade_date || !m.stock_code) continue;
      const result = await env.DB.prepare(`
        INSERT OR REPLACE INTO stock_metrics
          (trade_date, stock_code, stock_name, shareholder_count,
           shareholder_change_pct, main_force, main_force_label,
           chip_focus, report_period, top10_float_ratio, price, announcement_date,
           week_profit, month_profit, quarter_profit, change_percent,
           industry, sector, financials, theme_concepts, quality_shareholder)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).bind(
        m.trade_date, m.stock_code || null, m.stock_name || null,
        m.shareholder_count ?? null, m.shareholder_change_pct ?? null,
        m.main_force ?? null, m.main_force_label || null,
        m.chip_focus || null, m.report_period || null,
        m.top10_float_ratio ?? null, m.price ?? null,
        m.announcement_date || null,
        m.week_profit ?? null, m.month_profit ?? null, m.quarter_profit ?? null,
        m.change_percent ?? null,
        m.industry || null, m.sector || null,
        m.financials ? JSON.stringify(m.financials) : null,
        m.theme_concepts ? JSON.stringify(m.theme_concepts) : null,
        m.quality_shareholder ? 1 : 0,
      ).run();
      const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
      if (changes > 0) inserted++;
    }
    return json({ ok: true, inserted, total: metrics.length });
  }

  return json({ ok: false, error: 'method not allowed' }, 405);
}