import { isSubscribed, isAdmin } from '../../../_lib/subscription-auth.js';

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

const CLIENT_METRIC_FIELDS = [
  'trade_date', 'stock_code', 'stock_name', 'shareholder_count', 'shareholder_change_pct',
  'main_force', 'main_force_label', 'concentration90', 'chip_focus', 'report_period',
  'top10_float_ratio', 'price', 'announcement_date', 'change_percent', 'industry', 'sector',
  'financials', 'theme_concepts', 'quality_shareholder', 'shareholder_nature',
];
const LOW_CHIP_MEMBERSHIP_SQL = 'week_profit IS NOT NULL AND month_profit IS NOT NULL AND quarter_profit IS NOT NULL';

function clientMetric(row) {
  const metric = Object.fromEntries(CLIENT_METRIC_FIELDS.filter((field) => field in row).map((field) => [field, row[field]]));
  if (typeof metric.shareholder_nature === 'string') {
    try { metric.shareholder_nature = JSON.parse(metric.shareholder_nature); } catch (e) { metric.shareholder_nature = null; }
  }
  return metric;
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
        shareholder_nature TEXT,
        pe_ttm REAL,
        pb REAL,
        ps_ttm REAL,
        pcf_ttm REAL,
        total_share REAL,
        total_mv REAL,
        fundamental_shadow_status TEXT,
        fundamental_shadow_sessions INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, stock_code)
      )
    `).run();
    // Backward-compatible column add
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN announcement_date TEXT').run(); } catch (e) {}
  };

  if (method === 'GET') {
    const syncToken = String(env.LOW_CHIP_SYNC_TOKEN || '').trim();
    const serviceAuthenticated = syncToken
      && String(request.headers.get('authorization') || '') === `Bearer ${syncToken}`;
    if (!serviceAuthenticated && !(await isSubscribed(request, env)) && !(await isAdmin(request, env))) {
      return json({ ok: false, error: '需要登录' }, 401);
    }
    const rawTradeDate = String(url.searchParams.get('date') || '');
    const tradeDate = /^\d{4}-\d{2}-\d{2}$/.test(rawTradeDate)
      ? rawTradeDate.replace(/-/g, '')
      : rawTradeDate;
    const code = url.searchParams.get('code');
    if (!/^\d{8}$/.test(tradeDate)) return json({ ok: false, error: 'date parameter must be YYYY-MM-DD or YYYYMMDD' }, 400);
    await ensureTable();
    let results;
    if (code) {
      const r = await env.DB.prepare(
        `SELECT * FROM stock_metrics WHERE trade_date = ? AND stock_code = ? AND ${LOW_CHIP_MEMBERSHIP_SQL}`
      ).bind(tradeDate, code).all();
      results = (r.results || []).map(clientMetric);
    } else {
      const r = await env.DB.prepare(
        `SELECT * FROM stock_metrics WHERE trade_date = ? AND ${LOW_CHIP_MEMBERSHIP_SQL} ORDER BY main_force DESC`
      ).bind(tradeDate).all();
      results = (r.results || []).map(clientMetric);
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
    const preserveExisting = body?.preserve_existing === true;
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
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN shareholder_nature TEXT').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN closing_profit REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN average_cost REAL').run(); } catch (e) {}
    try { await env.DB.prepare('ALTER TABLE stock_metrics ADD COLUMN conc70 REAL').run(); } catch (e) {}
    for (const [column, type] of [
      ['pe_ttm', 'REAL'], ['pb', 'REAL'], ['ps_ttm', 'REAL'], ['pcf_ttm', 'REAL'],
      ['total_share', 'REAL'], ['total_mv', 'REAL'], ['fundamental_shadow_status', 'TEXT'],
      ['fundamental_shadow_sessions', 'INTEGER'],
    ]) {
      try { await env.DB.prepare(`ALTER TABLE stock_metrics ADD COLUMN ${column} ${type}`).run(); } catch (e) {}
    }
    let inserted = 0;
    // 批量写入：D1 prepared statement 参数上限约100；32列×3行=96参数。
    const ROWS_PER_STMT = 3;
    const STMTS_PER_BATCH = 100;
    const cols = ['trade_date', 'stock_code', 'stock_name', 'shareholder_count',
      'shareholder_change_pct', 'main_force', 'main_force_label',
      'chip_focus', 'report_period', 'top10_float_ratio', 'price', 'announcement_date',
      'week_profit', 'month_profit', 'quarter_profit', 'change_percent',
      'industry', 'sector', 'financials', 'theme_concepts', 'quality_shareholder', 'shareholder_nature',
      'closing_profit', 'average_cost', 'conc70',
      'pe_ttm', 'pb', 'ps_ttm', 'pcf_ttm', 'total_share', 'total_mv',
      'fundamental_shadow_status', 'fundamental_shadow_sessions'];
    const rowValues = (m) => [
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
      m.shareholder_nature ? JSON.stringify(m.shareholder_nature) : null,
      m.closing_profit ?? null, m.average_cost ?? null, m.conc70 ?? null,
      m.pe_ttm ?? null, m.pb ?? null, m.ps_ttm ?? null, m.pcf_ttm ?? null,
      m.total_share ?? null, m.total_mv ?? null,
      m.fundamental_shadow_status || null, m.fundamental_shadow_sessions ?? null,
    ];
    const invalid = metrics.filter((m) => !m || !/^\d{8}$/.test(String(m.trade_date || '')) || !/^\d{6}$/.test(String(m.stock_code || '')));
    if (invalid.length) return json({ ok: false, error: 'every metric requires YYYYMMDD trade_date and six-digit stock_code' }, 400);
    const valid = metrics;
    const stmts = [];
    for (let i = 0; i < valid.length; i += ROWS_PER_STMT) {
      const chunk = valid.slice(i, i + ROWS_PER_STMT);
      const placeholders = chunk.map(() => `(${cols.map(() => '?').join(',')})`).join(',');
      const insertPrefix = preserveExisting ? 'INSERT INTO' : 'INSERT OR REPLACE INTO';
      const conflict = preserveExisting ? ` ON CONFLICT (trade_date, stock_code) DO UPDATE SET ${[
        'stock_name', 'price', 'pe_ttm', 'pb', 'ps_ttm', 'pcf_ttm', 'total_share', 'total_mv',
        'fundamental_shadow_status', 'fundamental_shadow_sessions',
      ].map((column) => `${column}=excluded.${column}`).join(',')}` : '';
      stmts.push(env.DB.prepare(
        `${insertPrefix} stock_metrics (${cols.join(',')}) VALUES ${placeholders}${conflict}`
      ).bind(...chunk.flatMap(rowValues)));
    }
    for (let i = 0; i < stmts.length; i += STMTS_PER_BATCH) {
      const part = stmts.slice(i, i + STMTS_PER_BATCH);
      const results = await env.DB.batch(part);
      for (const r of results) {
        const changes = Number(r?.meta?.changes ?? r?.changes ?? 0);
        if (changes > 0) inserted += changes;
      }
    }
    return json({ ok: true, inserted, total: metrics.length });
  }

  return json({ ok: false, error: 'method not allowed' }, 405);
}