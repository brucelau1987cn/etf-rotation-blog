// Shared buy-stock holdings access: schema bootstrap, normalisation, and reads.
//
// The holdings live in D1 rather than a committed JSON file because the repo
// is public on GitHub — real positions and their cost basis must not ship in
// the build output. Fee maths lives in src/lib/buy-stock-fees.mjs and is
// duplicated here on purpose: Pages Functions bundling cannot reach into
// src/ from functions/ without a path-depth change, and the two copies are
// locked by tests/buy_stock_fees.test.mjs.

export const POSITION_COLUMNS = [
  'code',
  'name',
  'market',
  'buy_price',
  'shares',
  'buy_time',
  'buy_date',
  'note',
  'sold_at',
  'sell_price',
  'sell_time',
];

export async function ensureBuyStockTable(db) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS buy_stock_positions (
      code TEXT PRIMARY KEY,
      name TEXT,
      market TEXT,
      buy_price REAL NOT NULL,
      shares INTEGER NOT NULL,
      buy_time TEXT,
      buy_date TEXT,
      note TEXT,
      sold_at TEXT,
      sell_price REAL,
      sell_time TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `).run();
  await db.prepare(`
    CREATE INDEX IF NOT EXISTS idx_buy_stock_positions_open
    ON buy_stock_positions (sold_at, buy_date)
  `).run();
}

function textOrEmpty(value) {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

function optionalNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Normalise a raw position row for the API. Returns { error } on invalid input.
 */
export function normalizePosition(row, { forCreate = false } = {}) {
  const code = textOrEmpty(row?.code).toUpperCase();
  if (!/^\d{6}$/.test(code)) return { error: '代码必须是6位数字' };

  const buyPrice = optionalNumber(row?.buy_price);
  if (buyPrice === null || buyPrice <= 0) return { error: '买入价必须大于0' };

  const shares = Number(row?.shares);
  if (!Number.isInteger(shares) || shares <= 0) return { error: '股数必须是正整数' };

  let buyTime = textOrEmpty(row?.buy_time);
  if (!buyTime) return { error: '买入时间必填' };
  // Accept "2026-10-14 14:03" and "10/14 14:03"; normalise to "YYYY-MM-DD HH:MM".
  let normalized = buyTime.replace(/\//g, '-');
  const m = normalized.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?/);
  if (m) {
    const [, y, mo, d, hh = '00', mi = '00'] = m;
    normalized = `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')} ${hh.padStart(2, '0')}:${mi}`;
  } else {
    const short = normalized.match(/^(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?/);
    if (!short) return { error: '买入时间格式无法识别' };
    const [, mo, d, hh = '00', mi = '00'] = short;
    const year = String(new Date().getUTCFullYear());
    normalized = `${year}-${mo.padStart(2, '0')}-${d.padStart(2, '0')} ${hh.padStart(2, '0')}:${mi}`;
  }
  const buyDate = normalized.slice(0, 10);

  const market = textOrEmpty(row?.market).toUpperCase()
    || (code.startsWith('6') ? 'SH' : code.startsWith('0') || code.startsWith('3') ? 'SZ' : '');

  const position = {
    code,
    name: textOrEmpty(row?.name),
    market,
    buy_price: buyPrice,
    shares,
    buy_time: normalized,
    buy_date: buyDate,
    note: textOrEmpty(row?.note) || null,
    sold_at: textOrEmpty(row?.sold_at) || null,
    sell_price: optionalNumber(row?.sell_price),
    sell_time: textOrEmpty(row?.sell_time) || null,
  };

  if (forCreate && !position.name) position.name = code;
  return { position };
}

/**
 * List positions, open ones first, then by buy date ascending.
 */
export async function listPositions(db, { includeClosed = true } = {}) {
  const where = includeClosed ? '' : 'WHERE sold_at IS NULL';
  const { results } = await db.prepare(
    `SELECT ${POSITION_COLUMNS.join(', ')} FROM buy_stock_positions ${where} ORDER BY sold_at IS NOT NULL, buy_date ASC, code ASC`,
  ).all();
  return results || [];
}
