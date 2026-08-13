/**
 * D1 helpers for rolling signals.
 * First write of (trade_date, symbol, cycle_code, signal) wins for the day.
 */

export const normalizeSymbol = value => {
  let str = String(value || '').trim().toUpperCase().replace(/\.(SH|SZ|SS|HK|US)$/i, '');
  if (/^\d{4}$/.test(str)) {
    return '0' + str;
  }
  return str;
};

export const shanghaiTradeDate = (input = new Date()) => {
  const date = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(date.getTime())) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date());
  }
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(date);
};

export const normalizeTriggerPrice = (value) => {
  const price = Number(value);
  if (!Number.isFinite(price) || price <= 0) return null;
  return Math.round(price * 10000) / 10000;
};

export const ensureRollingSignalsTable = async db => {
  if (!db?.prepare) return;
  // Support both D1 styles: prepare().run() and prepare().bind().run()
  const exec = async sql => {
    const stmt = db.prepare(sql);
    if (typeof stmt.run === 'function') return stmt.run();
    if (typeof stmt.bind === 'function') return stmt.bind().run();
    return null;
  };
  await exec(`
    CREATE TABLE IF NOT EXISTS rolling_signals (
      trade_date TEXT NOT NULL,
      symbol TEXT NOT NULL,
      cycle_code TEXT NOT NULL,
      signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL')),
      trigger_time_utc TEXT NOT NULL,
      received_at TEXT NOT NULL,
      event_id TEXT NOT NULL,
      label TEXT,
      instrument_name TEXT,
      exchange TEXT,
      trigger_price REAL,
      trigger_price_source TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (trade_date, symbol, cycle_code, signal)
    )
  `);
  await exec(`
    CREATE INDEX IF NOT EXISTS idx_rolling_signals_symbol_date
      ON rolling_signals (symbol, trade_date, received_at)
  `);
  // Backward-compatible upgrades for existing D1 tables.
  for (const sql of [
    'ALTER TABLE rolling_signals ADD COLUMN trigger_price REAL',
    'ALTER TABLE rolling_signals ADD COLUMN trigger_price_source TEXT',
  ]) {
    try {
      await exec(sql);
    } catch {
      // Column already exists — ignore.
    }
  }
};

/**
 * Insert once. Returns { inserted: boolean, row }
 */
export const insertRollingSignalOnce = async (db, row) => {
  await ensureRollingSignalsTable(db);
  const tradeDate = row.trade_date || shanghaiTradeDate(row.trigger_time_utc || row.received_at || new Date());
  const symbol = normalizeSymbol(row.symbol);
  const cycleCode = String(row.cycle_code || row.code || '').trim();
  const signal = String(row.signal || row.type || '').toUpperCase();
  const triggerTime = row.trigger_time_utc || row.triggered_at || row.received_at;
  const receivedAt = row.received_at || new Date().toISOString();
  const eventId = row.event_id || `evt_${Date.now()}`;
  const label = row.label || cycleCode;
  const triggerPrice = normalizeTriggerPrice(row.trigger_price ?? row.price);
  const triggerPriceSource = triggerPrice == null
    ? null
    : (String(row.trigger_price_source || row.price_source || 'webhook').trim() || 'webhook');

  const result = await db.prepare(`
    INSERT OR IGNORE INTO rolling_signals
      (trade_date, symbol, cycle_code, signal, trigger_time_utc, received_at, event_id, label, instrument_name, exchange, trigger_price, trigger_price_source)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    tradeDate,
    symbol,
    cycleCode,
    signal,
    triggerTime,
    receivedAt,
    eventId,
    label,
    row.instrument_name || null,
    row.exchange || null,
    triggerPrice,
    triggerPriceSource,
  ).run();

  const existing = await db.prepare(`
    SELECT trade_date, symbol, cycle_code, signal, trigger_time_utc, received_at, event_id, label, instrument_name, exchange, trigger_price, trigger_price_source
    FROM rolling_signals
    WHERE trade_date = ? AND symbol = ? AND cycle_code = ? AND signal = ?
  `).bind(tradeDate, symbol, cycleCode, signal).first();

  const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
  return {
    inserted: changes > 0,
    trade_date: tradeDate,
    row: existing || {
      trade_date: tradeDate,
      symbol,
      cycle_code: cycleCode,
      signal,
      trigger_time_utc: triggerTime,
      received_at: receivedAt,
      event_id: eventId,
      label,
      instrument_name: row.instrument_name || null,
      exchange: row.exchange || null,
      trigger_price: triggerPrice,
      trigger_price_source: triggerPriceSource,
    },
  };
};

/** Fill a missing trigger price for the exact first-write row. Never overwrite a locked price. */
export const updateRollingSignalPriceIfMissing = async (db, {
  trade_date,
  symbol,
  cycle_code,
  signal,
  event_id,
  trigger_price,
  trigger_price_source,
}) => {
  const price = normalizeTriggerPrice(trigger_price);
  if (!db?.prepare || price == null) return { updated: false, row: null };
  const key = normalizeSymbol(symbol);
  const aliases = [key];
  if (/^\d{5}$/.test(key) && key.startsWith('0')) {
    aliases.push(key.slice(1));
  }
  const placeholders = aliases.map(() => '?').join(', ');
  const source = String(trigger_price_source || 'kline-1m').trim() || 'kline-1m';
  const result = await db.prepare(`
    UPDATE rolling_signals
    SET trigger_price = ?, trigger_price_source = ?
    WHERE trade_date = ? AND symbol IN (${placeholders}) AND cycle_code = ? AND signal = ?
      AND event_id = ? AND trigger_price IS NULL
  `).bind(
    price,
    source,
    trade_date,
    ...aliases,
    String(cycle_code || '').trim(),
    String(signal || '').toUpperCase(),
    event_id,
  ).run();
  const row = await db.prepare(`
    SELECT trade_date, symbol, cycle_code, signal, trigger_time_utc, received_at, event_id, label, instrument_name, exchange, trigger_price, trigger_price_source
    FROM rolling_signals
    WHERE trade_date = ? AND symbol IN (${placeholders}) AND cycle_code = ? AND signal = ?
  `).bind(trade_date, ...aliases, String(cycle_code || '').trim(), String(signal || '').toUpperCase()).first();
  const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
  return { updated: changes > 0, row };
};

const mapTimelineRow = item => ({
  type: item.signal,
  code: String(item.cycle_code),
  label: String(item.label || item.cycle_code),
  triggered_at: item.trigger_time_utc || item.received_at,
  received_at: item.received_at || item.trigger_time_utc,
  event_id: item.event_id || null,
  price: normalizeTriggerPrice(item.trigger_price),
  price_source: item.trigger_price_source || null,
});

export const loadRollingTimelinesFromD1 = async (db, symbols = [], tradeDate = null) => {
  const keys = [...new Set((symbols || []).map(normalizeSymbol).filter(Boolean))];
  const grouped = new Map(keys.map(key => [key, []]));
  if (!db?.prepare || !keys.length) return grouped;
  await ensureRollingSignalsTable(db);
  const aliases = [];
  const aliasToKey = new Map();
  for (const key of keys) {
    const list = [key];
    if (/^\d{5}$/.test(key) && key.startsWith('0')) list.push(key.slice(1));
    for (const alias of list) {
      if (aliasToKey.has(alias)) continue;
      aliasToKey.set(alias, key);
      aliases.push(alias);
    }
  }
  const placeholders = aliases.map(() => '?').join(', ');
  const params = tradeDate ? [...aliases, tradeDate] : aliases;
  const { results } = tradeDate
    ? await db.prepare(`
        SELECT symbol, cycle_code, signal, trigger_time_utc, received_at, event_id, label, trigger_price, trigger_price_source
        FROM rolling_signals
        WHERE symbol IN (${placeholders}) AND trade_date = ?
        ORDER BY received_at ASC, trigger_time_utc ASC
      `).bind(...params).all()
    : await db.prepare(`
        SELECT symbol, cycle_code, signal, trigger_time_utc, received_at, event_id, label, trigger_price, trigger_price_source
        FROM rolling_signals
        WHERE symbol IN (${placeholders})
        ORDER BY received_at ASC, trigger_time_utc ASC
      `).bind(...params).all();

  for (const item of results || []) {
    const key = aliasToKey.get(normalizeSymbol(item.symbol)) || normalizeSymbol(item.symbol);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(mapTimelineRow(item));
  }
  return grouped;
};

export const loadRollingTimelineFromD1 = async (db, symbol, tradeDate = null) => {
  const key = normalizeSymbol(symbol);
  const grouped = await loadRollingTimelinesFromD1(db, [key], tradeDate);
  return grouped.get(key) || [];
};
