// Shared futures watchlist helpers (D1-backed).

export const DEFAULT_FUTURES_WATCHLIST = [
  { code: 'LC', continuous: 'LC0', name: '碳酸锂', exchange: '广期所', unit: '元/吨', tick: 20, edge_symbol: 'nf_LC0', sort_order: 10 },
  { code: 'PS', continuous: 'PS0', name: '多晶硅', exchange: '广期所', unit: '元/吨', tick: 5, edge_symbol: 'nf_PS0', sort_order: 20 },
  { code: 'SI', continuous: 'SI0', name: '工业硅', exchange: '广期所', unit: '元/吨', tick: 5, edge_symbol: 'nf_SI0', sort_order: 30 },
  { code: 'AU', continuous: 'AU0', name: '黄金', exchange: '上期所', unit: '元/克', tick: 0.02, edge_symbol: 'nf_AU0', sort_order: 40 },
  { code: 'AG', continuous: 'AG0', name: '白银', exchange: '上期所', unit: '元/千克', tick: 1, edge_symbol: 'nf_AG0', sort_order: 50 },
  { code: 'CU', continuous: 'CU0', name: '沪铜', exchange: '上期所', unit: '元/吨', tick: 10, edge_symbol: 'nf_CU0', sort_order: 60 },
  { code: 'AL', continuous: 'AL0', name: '沪铝', exchange: '上期所', unit: '元/吨', tick: 5, edge_symbol: 'nf_AL0', sort_order: 70 },
  { code: 'SC', continuous: 'SC0', name: '原油', exchange: '能源中心', unit: '元/桶', tick: 0.1, edge_symbol: 'nf_SC0', sort_order: 80 },
  { code: 'LH', continuous: 'LH0', name: '生猪', exchange: '大商所', unit: '元/吨', tick: 1, edge_symbol: 'nf_LH0', sort_order: 90 },
  { code: 'JM', continuous: 'JM0', name: '焦煤', exchange: '大商所', unit: '元/吨', tick: 0.5, edge_symbol: 'nf_JM0', sort_order: 100 },
];

export async function ensureFuturesWatchlistTable(db) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS futures_watchlist (
      code TEXT PRIMARY KEY,
      continuous TEXT NOT NULL,
      name TEXT NOT NULL,
      exchange TEXT,
      unit TEXT,
      tick REAL,
      edge_symbol TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `).run();
}

export async function seedFuturesWatchlistIfEmpty(db) {
  await ensureFuturesWatchlistTable(db);
  const row = await db.prepare('SELECT COUNT(*) AS n FROM futures_watchlist').first();
  if (Number(row?.n || 0) > 0) return { seeded: false, count: Number(row.n) };
  for (const item of DEFAULT_FUTURES_WATCHLIST) {
    await db.prepare(`
      INSERT INTO futures_watchlist
        (code, continuous, name, exchange, unit, tick, edge_symbol, sort_order, enabled)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    `).bind(
      item.code,
      item.continuous,
      item.name,
      item.exchange,
      item.unit,
      item.tick,
      item.edge_symbol,
      item.sort_order,
    ).run();
  }
  return { seeded: true, count: DEFAULT_FUTURES_WATCHLIST.length };
}

export function normalizeWatchlistItem(input = {}, { forCreate = false } = {}) {
  const code = String(input.code || '').trim().toUpperCase();
  if (!/^[A-Z]{1,4}$/.test(code)) {
    return { error: 'code 须为 1–4 位大写字母，例如 JM / AU' };
  }
  const continuous = String(input.continuous || `${code}0`).trim().toUpperCase();
  if (!/^[A-Z0-9]{2,8}$/.test(continuous)) {
    return { error: 'continuous 无效，例如 JM0' };
  }
  const name = String(input.name || '').trim();
  if (!name) return { error: 'name 必填' };
  const exchange = String(input.exchange || '').trim() || '未知交易所';
  const unit = String(input.unit || '').trim() || '元';
  const tick = Number(input.tick);
  if (!Number.isFinite(tick) || tick <= 0) return { error: 'tick 须为正数' };
  const edge_symbol = String(input.edge_symbol || `nf_${continuous}`).trim();
  if (!/^nf_[A-Za-z0-9]+$/.test(edge_symbol)) {
    return { error: 'edge_symbol 须为 nf_ 前缀，例如 nf_JM0' };
  }
  const sort_order = Number.isFinite(Number(input.sort_order))
    ? Math.trunc(Number(input.sort_order))
    : (forCreate ? 999 : 0);
  const enabled = input.enabled === 0 || input.enabled === false || input.enabled === '0' ? 0 : 1;
  return {
    item: {
      code,
      continuous,
      name,
      exchange,
      unit,
      tick,
      edge_symbol,
      sort_order,
      enabled,
    },
  };
}

export async function listFuturesWatchlist(db, { enabledOnly = false } = {}) {
  await seedFuturesWatchlistIfEmpty(db);
  const sql = enabledOnly
    ? `SELECT code, continuous, name, exchange, unit, tick, edge_symbol, sort_order, enabled, created_at, updated_at
       FROM futures_watchlist WHERE enabled = 1 ORDER BY sort_order ASC, code ASC`
    : `SELECT code, continuous, name, exchange, unit, tick, edge_symbol, sort_order, enabled, created_at, updated_at
       FROM futures_watchlist ORDER BY sort_order ASC, code ASC`;
  const rows = await db.prepare(sql).all();
  return (rows.results || []).map((row) => ({
    ...row,
    tick: Number(row.tick),
    sort_order: Number(row.sort_order),
    enabled: Number(row.enabled) === 1,
  }));
}
