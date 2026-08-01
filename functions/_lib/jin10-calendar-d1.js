import { insertRollingSignalOnce } from './rolling-signals-d1.js';

const ITEM_TYPES = new Set(['data', 'event', 'holiday', 'other']);

const itemKey = (item) => `${ITEM_TYPES.has(item.type) ? item.type : 'other'}:${item.id ?? `${item.indicator_id || 'na'}:${item.time || 'na'}`}`;
const nullableText = (value) => value === null || value === undefined || value === '' ? null : String(value);

const beijingToIso = (value) => {
  const text = String(value || '').trim();
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?$/.test(text)) return null;
  return new Date(`${text.replace(' ', 'T')}${text.length === 16 ? ':00' : ''}+08:00`).toISOString();
};

export const ensureJin10Tables = async (db) => {
  if (!db?.prepare) throw new Error('DB binding missing');
  const statements = [
    `CREATE TABLE IF NOT EXISTS jin10_calendar_items (
      item_key TEXT PRIMARY KEY, item_type TEXT NOT NULL, source_id INTEGER, indicator_id INTEGER,
      event_time TEXT, country TEXT, star INTEGER, title TEXT NOT NULL, previous TEXT, consensus TEXT,
      actual TEXT, revised TEXT, unit TEXT, affect INTEGER, show_affect INTEGER, time_status TEXT,
      source TEXT, raw_json TEXT NOT NULL, synced_at TEXT NOT NULL
    )`,
    `CREATE INDEX IF NOT EXISTS idx_jin10_calendar_time ON jin10_calendar_items (event_time, item_type, star)`,
    `CREATE TABLE IF NOT EXISTS jin10_asset_signals (
      signal_key TEXT PRIMARY KEY, calendar_item_key TEXT NOT NULL, source_id INTEGER, indicator_id INTEGER,
      signal_time TEXT NOT NULL, asset_class TEXT NOT NULL, symbol TEXT NOT NULL, display_name TEXT NOT NULL,
      direction TEXT NOT NULL, rolling_signal TEXT, rolling_code TEXT, label TEXT, previous TEXT,
      consensus TEXT, actual TEXT, unit TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )`,
    `CREATE INDEX IF NOT EXISTS idx_jin10_asset_signal_symbol_time ON jin10_asset_signals (symbol, signal_time, direction)`,
  ];
  for (const sql of statements) await db.prepare(sql).run();
};

/**
 * Jin10 App semantics verified for indicator_id=951:
 * affect=1 + show_affect=1 renders “利空 金银”.
 */
export const deriveAssetSignals = (item) => {
  if (item?.type !== 'data' || item.actual === null || item.actual === undefined || item.actual === '') return [];
  if (Number(item.indicator_id) !== 951 || Number(item.show_affect) !== 1) return [];
  const direction = Number(item.affect) === 1 ? 'bearish' : Number(item.affect) === 2 ? 'bullish' : 'neutral';
  if (direction === 'neutral') return [];
  const rollingSignal = direction === 'bearish' ? 'SELL' : 'BUY';
  const rollingCode = direction === 'bearish' ? '宏观利空' : '宏观利多';
  const label = `${item.country || ''}${item.title || '石油钻井数据'}：前值${nullableText(item.previous) ?? '—'}，公布${nullableText(item.actual) ?? '—'}，${rollingCode}金银`;
  return [
    { asset_class: 'futures', symbol: 'GC=F', display_name: '黄金期货' },
    { asset_class: 'spot-metal', symbol: 'SI=F', display_name: '白银现货' },
  ].map((asset) => ({
    ...asset,
    direction,
    rolling_signal: rollingSignal,
    rolling_code: rollingCode,
    label,
  }));
};

export const persistJin10Items = async (db, items, syncedAt = new Date().toISOString()) => {
  await ensureJin10Tables(db);
  let itemsUpserted = 0;
  let assetSignalsUpserted = 0;
  let rollingSignalsInserted = 0;
  for (const item of items || []) {
    const key = itemKey(item);
    await db.prepare(`
      INSERT INTO jin10_calendar_items
        (item_key,item_type,source_id,indicator_id,event_time,country,star,title,previous,consensus,actual,revised,unit,affect,show_affect,time_status,source,raw_json,synced_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(item_key) DO UPDATE SET
        item_type=excluded.item_type, indicator_id=excluded.indicator_id, event_time=excluded.event_time,
        country=excluded.country, star=excluded.star, title=excluded.title, previous=excluded.previous,
        consensus=excluded.consensus, actual=excluded.actual, revised=excluded.revised, unit=excluded.unit,
        affect=excluded.affect, show_affect=excluded.show_affect, time_status=excluded.time_status,
        source=excluded.source, raw_json=excluded.raw_json, synced_at=excluded.synced_at
    `).bind(
      key, ITEM_TYPES.has(item.type) ? item.type : 'other', item.id ?? null, item.indicator_id ?? null,
      item.time ?? null, nullableText(item.country), item.star ?? null, String(item.title || '未命名事项'),
      nullableText(item.previous), nullableText(item.consensus), nullableText(item.actual), nullableText(item.revised),
      nullableText(item.unit), item.affect ?? null, item.show_affect ?? null, nullableText(item.time_status),
      nullableText(item.source), JSON.stringify(item), syncedAt,
    ).run();
    itemsUpserted += 1;

    for (const signal of deriveAssetSignals(item)) {
      const signalKey = `${key}:${signal.symbol}:${signal.direction}`;
      await db.prepare(`
        INSERT INTO jin10_asset_signals
          (signal_key,calendar_item_key,source_id,indicator_id,signal_time,asset_class,symbol,display_name,direction,rolling_signal,rolling_code,label,previous,consensus,actual,unit,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_key) DO UPDATE SET direction=excluded.direction, rolling_signal=excluded.rolling_signal,
          rolling_code=excluded.rolling_code, label=excluded.label, previous=excluded.previous,
          consensus=excluded.consensus, actual=excluded.actual, unit=excluded.unit, updated_at=excluded.updated_at
      `).bind(
        signalKey, key, item.id ?? null, item.indicator_id ?? null, item.time, signal.asset_class,
        signal.symbol, signal.display_name, signal.direction, signal.rolling_signal, signal.rolling_code,
        signal.label, nullableText(item.previous), nullableText(item.consensus), nullableText(item.actual),
        nullableText(item.unit), syncedAt, syncedAt,
      ).run();
      assetSignalsUpserted += 1;

      if (signal.symbol === 'SI=F') {
        const triggerIso = beijingToIso(item.time) || syncedAt;
        const inserted = await insertRollingSignalOnce(db, {
          trade_date: String(item.time || '').slice(0, 10),
          symbol: 'SI=F',
          cycle_code: signal.rolling_code,
          signal: signal.rolling_signal,
          trigger_time_utc: triggerIso,
          received_at: syncedAt,
          event_id: `jin10:${item.id}`,
          label: signal.label,
          instrument_name: '白银现货',
          exchange: 'FUTURES',
        });
        if (inserted.inserted) rollingSignalsInserted += 1;
      }
    }
  }
  return {
    items_upserted: itemsUpserted,
    asset_signals_upserted: assetSignalsUpserted,
    rolling_signals_inserted: rollingSignalsInserted,
  };
};
