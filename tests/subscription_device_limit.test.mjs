import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { acquireSession, MAX_DEVICES } from '../functions/_lib/subscription-auth.js';

class DeviceDb {
  constructor(activeCount) {
    this.activeCount = activeCount;
    this.inserted = 0;
  }

  prepare(sql) {
    const db = this;
    return {
      bind() { return this; },
      async first() {
        if (/SELECT sid FROM sub_sessions/.test(sql)) return null;
        if (/COUNT\(\*\)/.test(sql)) return { n: db.activeCount };
        return null;
      },
      async run() {
        if (/INSERT INTO sub_sessions/.test(sql)) {
          if (db.activeCount >= MAX_DEVICES) return { meta: { changes: 0 } };
          db.inserted += 1;
          db.activeCount += 1;
          return { meta: { changes: 1 } };
        }
        return { meta: { changes: 1 } };
      },
    };
  }
}

test('subscription permits the tenth device', async () => {
  const db = new DeviceDb(9);
  const result = await acquireSession(
    { DB: db }, 1, '2099-12-31T00:00:00.000Z', 'device-10', 'test-agent',
  );
  assert.equal(MAX_DEVICES, 10);
  assert.equal(result.limitHit, undefined);
  assert.equal(result.deviceCount, 10);
  assert.equal(db.inserted, 1);
});

test('subscription rejects the eleventh device', async () => {
  const db = new DeviceDb(10);
  const result = await acquireSession(
    { DB: db }, 1, '2099-12-31T00:00:00.000Z', 'device-11', 'test-agent',
  );
  assert.equal(result.limitHit, true);
  assert.equal(result.deviceCount, 10);
  assert.equal(db.inserted, 0);
});

class ConcurrentDb {
  constructor(activeCount = 9) {
    this.activeCount = activeCount;
    this.sessions = new Map();
    this.inserted = 0;

  }

  prepare(sql) {
    const db = this;
    const stmt = {
      args: [],
      bind(...args) { stmt.args = args; return stmt; },
      async first() {
        if (/SELECT sid FROM sub_sessions/.test(sql)) {
          return db.sessions.get(String(stmt.args[1])) || null;
        }
        if (/COUNT\(\*\)/.test(sql)) return { n: db.activeCount };
        return null;
      },
      async run() {
        if (/INSERT INTO sub_sessions/.test(sql)) {
          const deviceId = String(stmt.args[1]);
          if (db.sessions.has(deviceId) || db.activeCount >= MAX_DEVICES) {
            return { meta: { changes: 0 } };
          }
          db.sessions.set(deviceId, { sid: String(stmt.args[3]) });
          db.activeCount += 1;
          db.inserted += 1;
          return { meta: { changes: 1 } };
        }
        return { meta: { changes: 1 } };
      },
    };
    return stmt;
  }
}

test('concurrent devices cannot both claim the tenth slot', async () => {
  const db = new ConcurrentDb(9);
  const results = await Promise.all([
    acquireSession({ DB: db }, 1, '2099-12-31T00:00:00.000Z', 'device-a', 'agent-a'),
    acquireSession({ DB: db }, 1, '2099-12-31T00:00:00.000Z', 'device-b', 'agent-b'),
  ]);
  assert.equal(db.inserted, 1);
  assert.equal(db.activeCount, 10);
  assert.equal(results.filter((item) => item.limitHit).length, 1);
});

test('concurrent first login for one device creates one session', async () => {
  const db = new ConcurrentDb(0);
  const results = await Promise.all([
    acquireSession({ DB: db }, 1, '2099-12-31T00:00:00.000Z', 'same-device', 'agent-a'),
    acquireSession({ DB: db }, 1, '2099-12-31T00:00:00.000Z', 'same-device', 'agent-b'),
  ]);
  assert.equal(db.inserted, 1);
  assert.equal(db.sessions.size, 1);
  assert.equal(results.filter((item) => item.limitHit).length, 0);
  assert.equal(new Set(results.map((item) => item.sid)).size, 1);
});

test('new migration enforces one row per subscription device', async () => {
  const sql = await readFile(new URL('../migrations/0017_sub_session_device_uniqueness.sql', import.meta.url), 'utf8');
  assert.match(sql, /CREATE UNIQUE INDEX/i);
  assert.match(sql, /subscription_id\s*,\s*device_id/i);
});
