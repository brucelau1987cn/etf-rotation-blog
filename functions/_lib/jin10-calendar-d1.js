const ITEM_TYPES = new Set(['data', 'event', 'holiday', 'other']);

const itemKey = (item) => `${ITEM_TYPES.has(item.type) ? item.type : 'other'}:${item.id ?? `${item.indicator_id || 'na'}:${item.time || 'na'}`}`;
const nullableText = (value) => value === null || value === undefined || value === '' ? null : String(value);

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
  ];
  for (const sql of statements) await db.prepare(sql).run();
};

export const persistJin10Items = async (db, items, syncedAt = new Date().toISOString()) => {
  await ensureJin10Tables(db);
  let itemsUpserted = 0;
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
  }
  return {
    items_upserted: itemsUpserted,
    asset_signals_upserted: 0,
    rolling_signals_inserted: 0,
  };
};