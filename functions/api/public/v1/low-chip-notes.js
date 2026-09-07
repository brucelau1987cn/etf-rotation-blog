import { isSubscribed, isAdmin } from '../../../_lib/subscription-auth.js';

/**
 * /api/public/v1/low-chip-notes
 * AI 评语 + 短线反转观察星级 (1-3★), 与选股完全解耦的独立存储。
 *
 * GET  ?date=YYYY-MM-DD            -> 当日全部评语 (订阅/管理员)
 * GET  ?date=YYYY-MM-DD&code=xxx   -> 单只当日评语
 * GET  ?symbol=xxx                 -> 单只全部历史 (星级演变, 供未来页面)
 * POST { notes: [{trade_date, stock_code, stock_name, stars, label, comment, ...}] }
 *      Bearer LOW_CHIP_SYNC_TOKEN。主键 (stock_code, trade_date), INSERT OR REPLACE。
 *      单只每日一条, 不跨日覆盖历史。
 */

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

const NOTE_FIELDS = [
  'trade_date', 'stock_code', 'stock_name', 'stars', 'label', 'comment',
  'streak_days', 'model', 'status', 'created_at',
];

const VALID_LABELS = [
  '开始转强', '反转观察', '初现止跌', '相对强势', '趋势延续', '仍在寻底', '风险偏高', '数据不足',
];

function clientNote(row) {
  return Object.fromEntries(NOTE_FIELDS.filter((f) => f in row).map((f) => [f, row[f]]));
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const method = request.method;

  const ensureTable = async () => {
    if (!env.DB) return;
    await env.DB.prepare(`
      CREATE TABLE IF NOT EXISTS low_chip_ai_notes (
        trade_date TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        stars INTEGER,
        label TEXT,
        comment TEXT,
        streak_days INTEGER,
        model TEXT,
        status TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (stock_code, trade_date)
      )
    `).run();
  };

  if (method === 'GET') {
    const syncToken = String(env.LOW_CHIP_SYNC_TOKEN || '').trim();
    const serviceAuthenticated = syncToken
      && String(request.headers.get('authorization') || '') === `Bearer ${syncToken}`;
    if (!serviceAuthenticated && !(await isSubscribed(request, env)) && !(await isAdmin(request, env))) {
      return json({ ok: false, error: '需要登录' }, 401);
    }
    await ensureTable();

    const rawDate = String(url.searchParams.get('date') || '');
    const code = String(url.searchParams.get('code') || '');
    const symbol = String(url.searchParams.get('symbol') || '');

    if (symbol) {
      // 单只全部历史: ?symbol=002992.SZ 或 002992 (星级演变)
      const bare = symbol.replace(/\..*$/, '');
      const r = await env.DB.prepare(
        `SELECT * FROM low_chip_ai_notes WHERE stock_code = ? ORDER BY trade_date ASC`
      ).bind(bare).all();
      return json({ ok: true, symbol: bare, count: (r.results || []).length, results: (r.results || []).map(clientNote) });
    }

    const tradeDate = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate.replace(/-/g, '') : rawDate;
    if (!/^\d{8}$/.test(tradeDate)) {
      return json({ ok: false, error: 'date parameter must be YYYY-MM-DD or YYYYMMDD' }, 400);
    }
    let results;
    if (code) {
      const bare = code.replace(/\..*$/, '');
      const r = await env.DB.prepare(
        `SELECT * FROM low_chip_ai_notes WHERE trade_date = ? AND stock_code = ?`
      ).bind(tradeDate, bare).all();
      results = (r.results || []).map(clientNote);
    } else {
      const r = await env.DB.prepare(
        `SELECT * FROM low_chip_ai_notes WHERE trade_date = ? ORDER BY stars DESC, stock_code ASC`
      ).bind(tradeDate).all();
      results = (r.results || []).map(clientNote);
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
    const notes = body?.notes;
    if (!Array.isArray(notes) || notes.length === 0) {
      return json({ ok: false, error: 'notes array required' }, 400);
    }
    const invalid = notes.filter((n) => !n
      || !/^\d{8}$/.test(String(n.trade_date || ''))
      || !/^\d{6}$/.test(String(n.stock_code || ''))
      || ![1, 2, 3].includes(Number(n.stars))
      || !String(n.comment || '').trim());
    if (invalid.length) {
      return json({ ok: false, error: 'every note requires YYYYMMDD trade_date, six-digit stock_code, stars in {1,2,3}, non-empty comment' }, 400);
    }
    await ensureTable();
    const cols = ['trade_date', 'stock_code', 'stock_name', 'stars', 'label', 'comment', 'streak_days', 'model', 'status'];
    const rowValues = (n) => [
      String(n.trade_date), String(n.stock_code), n.stock_name || null,
      Number(n.stars),
      VALID_LABELS.includes(n.label) ? n.label : null,
      String(n.comment).trim(),
      n.streak_days ?? null,
      n.model || null,
      n.status || 'ok',
    ];
    // D1 单条 prepared 参数上限 100; 单行 9 参数
    const ROWS_PER_STMT = 8;
    const STMTS_PER_BATCH = 100;
    const stmts = [];
    for (let i = 0; i < notes.length; i += ROWS_PER_STMT) {
      const chunk = notes.slice(i, i + ROWS_PER_STMT);
      const placeholders = chunk.map(() => `(${cols.map(() => '?').join(',')})`).join(',');
      stmts.push(env.DB.prepare(
        `INSERT OR REPLACE INTO low_chip_ai_notes (${cols.join(',')}) VALUES ${placeholders}`
      ).bind(...chunk.flatMap(rowValues)));
    }
    let inserted = 0;
    for (let i = 0; i < stmts.length; i += STMTS_PER_BATCH) {
      const part = stmts.slice(i, i + STMTS_PER_BATCH);
      const results = await env.DB.batch(part);
      for (const r of results) {
        inserted += Number(r?.meta?.changes ?? r?.changes ?? 0);
      }
    }
    return json({ ok: true, inserted, total: notes.length });
  }

  return json({ ok: false, error: 'method not allowed' }, 405);
}
