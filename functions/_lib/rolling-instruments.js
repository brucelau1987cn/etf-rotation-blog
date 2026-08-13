// Shared rolling instruments helpers (D1-backed).
// Markets: a | futures | hk | us

export const ROLLING_MARKETS = [
  { key: 'a', label: 'A股', path: '/rolling/' },
  { key: 'futures', label: '期货', path: '/rolling/futures/' },
  { key: 'hk', label: '港股', path: '/rolling/hk/' },
  { key: 'us', label: '美股', path: '/rolling/us/' },
];

// Seed mirrors current production universe + start_date from LKG snapshots (2026-08-10).
export const DEFAULT_ROLLING_INSTRUMENTS = [
  { market: 'a', symbol: '600021', name: '上海电力', exchange: 'SSE', start_date: '2026-07-28', sort_order: 10 },
  { market: 'a', symbol: '002173', name: '创新医疗', exchange: 'SZSE', start_date: '2026-08-04', sort_order: 20 },
  { market: 'a', symbol: '600703', name: '三安光电', exchange: 'SSE', start_date: '2026-07-21', sort_order: 30 },
  { market: 'a', symbol: '000021', name: '深科技', exchange: 'SZSE', start_date: '2026-07-21', sort_order: 40 },
  { market: 'a', symbol: '301511', name: '德福科技', exchange: 'SZSE', start_date: '2026-07-21', sort_order: 50 },
  { market: 'a', symbol: '301362', name: '民爆光电', exchange: 'SZSE', start_date: '2026-07-21', sort_order: 60 },
  { market: 'a', symbol: '688041', name: '海光信息', exchange: 'SSE', start_date: '2026-08-04', sort_order: 70 },
  { market: 'a', symbol: '600637', name: '东方明珠', exchange: 'SSE', start_date: '2026-07-28', sort_order: 80 },
  { market: 'a', symbol: '688825', name: '长鑫科技', exchange: 'SSE', start_date: '2026-07-28', sort_order: 90 },
  { market: 'a', symbol: '300077', name: '国民技术', exchange: 'SZSE', start_date: '2026-07-28', sort_order: 100 },
  { market: 'a', symbol: '002185', name: '华天科技', exchange: 'SZSE', start_date: '2026-07-28', sort_order: 110 },
  { market: 'hk', symbol: '01378', name: '中国宏桥', exchange: 'HKEX', start_date: '2026-07-14', sort_order: 10 },
  { market: 'hk', symbol: '06809', name: '澜起科技', exchange: 'HKEX', start_date: '2026-07-30', sort_order: 20 },
  { market: 'us', symbol: 'TSLA', name: '特斯拉', exchange: 'NASDAQ', start_date: '2026-07-28', sort_order: 10 },
  { market: 'futures', symbol: 'SI=F', name: '白银现货', exchange: 'FUTURES', start_date: '2026-07-30', sort_order: 10, quote_symbol: 'hf_XAG' },
];

export async function ensureRollingInstrumentsTable(db) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS rolling_instruments (
      market TEXT NOT NULL,
      symbol TEXT NOT NULL,
      name TEXT NOT NULL,
      exchange TEXT,
      start_date TEXT NOT NULL,
      quote_symbol TEXT,
      snapshot_path TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1,
      reset_at TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (market, symbol)
    )
  `).run();
}

export async function seedRollingInstrumentsIfEmpty(db) {
  await ensureRollingInstrumentsTable(db);
  const row = await db.prepare('SELECT COUNT(*) AS n FROM rolling_instruments').first();
  if (Number(row?.n || 0) > 0) return { seeded: false, count: Number(row.n) };
  for (const item of DEFAULT_ROLLING_INSTRUMENTS) {
    await db.prepare(`
      INSERT OR IGNORE INTO rolling_instruments
        (market, symbol, name, exchange, start_date, quote_symbol, sort_order, enabled)
      VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    `).bind(
      item.market,
      item.symbol,
      item.name,
      item.exchange,
      item.start_date,
      item.quote_symbol || null,
      item.sort_order,
    ).run();
  }
  return { seeded: true, count: DEFAULT_ROLLING_INSTRUMENTS.length };
}

export function normalizeMarket(value) {
  const key = String(value || '').trim().toLowerCase();
  if (['a', 'a-share', 'ashare', 'cn'].includes(key)) return 'a';
  if (['futures', 'future', 'f'].includes(key)) return 'futures';
  if (['hk', 'hongkong', 'hkex'].includes(key)) return 'hk';
  if (['us', 'usa', 'nasdaq', 'nyse'].includes(key)) return 'us';
  return null;
}

export function normalizeRollingInstrument(input = {}, { forCreate = false } = {}) {
  const market = normalizeMarket(input.market);
  if (!market) return { error: 'market 须为 a / futures / hk / us' };

  let symbol = String(input.symbol || '').trim().toUpperCase();
  if (!symbol) return { error: 'symbol 必填' };
  // HK pad 4-digit
  if (market === 'hk' && /^\d{4}$/.test(symbol)) symbol = symbol.padStart(5, '0');
  if (market === 'a' && !/^\d{6}$/.test(symbol)) return { error: 'A股代码须为 6 位数字' };
  if (market === 'us' && !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(symbol)) return { error: '美股代码无效' };

  const name = String(input.name || '').trim();
  if (!name) return { error: 'name 必填' };

  const exchange = String(input.exchange || '').trim() || (
    market === 'a' ? (symbol.startsWith('6') ? 'SSE' : 'SZSE')
      : market === 'hk' ? 'HKEX'
        : market === 'us' ? 'NASDAQ'
          : 'FUTURES'
  );

  const start_date = String(input.start_date || '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start_date)) {
    return { error: 'start_date 须为 YYYY-MM-DD' };
  }

  const quote_symbol = String(input.quote_symbol || '').trim() || null;
  const sort_order = Number.isFinite(Number(input.sort_order))
    ? Math.trunc(Number(input.sort_order))
    : (forCreate ? 999 : 0);
  const enabled = input.enabled === 0 || input.enabled === false || input.enabled === '0' ? 0 : 1;

  return {
    item: {
      market,
      symbol,
      name,
      exchange,
      start_date,
      quote_symbol,
      sort_order,
      enabled,
    },
  };
}

export async function listRollingInstruments(db, { market = null, enabledOnly = false } = {}) {
  await seedRollingInstrumentsIfEmpty(db);
  const clauses = [];
  const binds = [];
  if (market) {
    clauses.push('market = ?');
    binds.push(market);
  }
  if (enabledOnly) clauses.push('enabled = 1');
  const where = clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
  const rows = await db.prepare(`
    SELECT market, symbol, name, exchange, start_date, quote_symbol, snapshot_path,
           sort_order, enabled, reset_at, created_at, updated_at
    FROM rolling_instruments
    ${where}
    ORDER BY market ASC, sort_order ASC, symbol ASC
  `).bind(...binds).all();
  return (rows.results || []).map((row) => ({
    ...row,
    sort_order: Number(row.sort_order),
    enabled: Number(row.enabled) === 1,
  }));
}

export async function getRollingInstrument(db, market, symbol) {
  await seedRollingInstrumentsIfEmpty(db);
  const m = normalizeMarket(market);
  const s = String(symbol || '').trim().toUpperCase();
  if (!m || !s) return null;
  const row = await db.prepare(`
    SELECT market, symbol, name, exchange, start_date, quote_symbol, snapshot_path,
           sort_order, enabled, reset_at, created_at, updated_at
    FROM rolling_instruments
    WHERE market = ? AND symbol = ?
    LIMIT 1
  `).bind(m, s).first();
  if (!row) return null;
  return {
    ...row,
    sort_order: Number(row.sort_order),
    enabled: Number(row.enabled) === 1,
  };
}

/** Find by symbol across markets (public signal path). Prefer exact, then HK unpadded. */
export async function findRollingInstrumentBySymbol(db, symbol, { seed = true } = {}) {
  if (seed) await seedRollingInstrumentsIfEmpty(db);
  const raw = String(symbol || '').trim().toUpperCase();
  if (!raw) return null;
  const candidates = [raw];
  if (/^\d{4}$/.test(raw)) candidates.push(raw.padStart(5, '0'));
  if (/^\d{5}$/.test(raw) && raw.startsWith('0')) candidates.push(raw.slice(1));
  // futures aliases
  if (raw === 'HF_XAG') candidates.push('SI=F');
  if (raw === 'SI=F') candidates.push('HF_XAG');

  for (const sym of candidates) {
    const row = await db.prepare(`
      SELECT market, symbol, name, exchange, start_date, quote_symbol, snapshot_path,
             sort_order, enabled, reset_at, created_at, updated_at
      FROM rolling_instruments
      WHERE symbol = ? OR quote_symbol = ?
      LIMIT 1
    `).bind(sym, sym).first();
    if (row) {
      return {
        ...row,
        sort_order: Number(row.sort_order),
        enabled: Number(row.enabled) === 1,
      };
    }
  }
  return null;
}

export async function clearRollingSignalsForSymbol(db, symbol) {
  const sym = String(symbol || '').trim().toUpperCase();
  if (!sym) return { deleted: 0 };
  // Also clear common aliases for HK/futures
  const aliases = new Set([sym]);
  if (/^\d{5}$/.test(sym) && sym.startsWith('0')) aliases.add(sym.slice(1));
  if (/^\d{4}$/.test(sym)) aliases.add(sym.padStart(5, '0'));
  if (sym === 'SI=F') aliases.add('HF_XAG');
  if (sym === 'HF_XAG') aliases.add('SI=F');

  let deleted = 0;
  for (const a of aliases) {
    const result = await db.prepare('DELETE FROM rolling_signals WHERE symbol = ?').bind(a).run();
    deleted += Number(result.meta?.changes || 0);
  }
  return { deleted };
}
