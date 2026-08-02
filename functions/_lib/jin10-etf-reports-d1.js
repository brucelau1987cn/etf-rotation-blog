/**
 * D1 persistence for Jin10 ETF holdings daily snapshots.
 *
 * Table: jin10_etf_holdings (reported_on, attr_id) PK.
 */

const ensureTable = async (db) => {
  if (!db?.prepare) throw new Error('DB binding missing');
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS jin10_etf_holdings (
      reported_on TEXT NOT NULL,
      attr_id INTEGER NOT NULL,
      trust REAL,
      change_value REAL,
      total_value REAL,
      raw_json TEXT NOT NULL,
      synced_at TEXT NOT NULL,
      PRIMARY KEY (reported_on, attr_id)
    )
  `).run();
  await db.prepare(`
    CREATE INDEX IF NOT EXISTS idx_jin10_etf_holdings_attr
      ON jin10_etf_holdings (attr_id, reported_on)
  `).run();
};

export const persistJin10EtfHoldings = async (db, attrId, rows) => {
  await ensureTable(db);
  let upserted = 0;
  for (const row of rows || []) {
    if (!row?.reported_on) continue;
    await db.prepare(`
      INSERT INTO jin10_etf_holdings (reported_on, attr_id, trust, change_value, total_value, raw_json, synced_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(reported_on, attr_id) DO UPDATE SET
        trust = excluded.trust,
        change_value = excluded.change_value,
        total_value = excluded.total_value,
        raw_json = excluded.raw_json,
        synced_at = excluded.synced_at
    `).bind(
      row.reported_on,
      attrId,
      row.trust ?? null,
      row.change ?? null,
      row.value ?? null,
      JSON.stringify(row),
      new Date().toISOString(),
    ).run();
    upserted += 1;
  }
  return { rows_upserted: upserted };
};

export const readJin10EtfHoldings = async (db, attrId, limit) => {
  await ensureTable(db);
  const result = await db.prepare(`
    SELECT reported_on, trust, change_value AS change, total_value AS value, raw_json, synced_at
    FROM jin10_etf_holdings
    WHERE attr_id = ?
    ORDER BY reported_on DESC
    LIMIT ?
  `).bind(attrId, limit).all();
  return (result?.results || []).map((r) => ({
    reported_on: r.reported_on,
    trust: r.trust,
    change: r.change,
    value: r.value,
    raw_json: r.raw_json,
    synced_at: r.synced_at,
  }));
};