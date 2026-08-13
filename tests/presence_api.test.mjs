import test from 'node:test';
import assert from 'node:assert/strict';
import { onRequestPost } from '../functions/api/public/v1/presence.js';

function createDb() {
  const active = new Map();
  const sqlCalls = [];
  return {
    active,
    sqlCalls,
    prepare(sql) {
      const stmt = {
        args: [],
        bind(...args) { this.args = args; return this; },
        async run() {
          sqlCalls.push({ sql, args: this.args });
          if (sql.includes('INSERT INTO presence_sessions')) {
            const [visitorId, lastSeen, ipKey] = this.args;
            if (sql.includes('SELECT ?, ?, ? WHERE')) {
              const [, , , limitIpKey, cutoff, limit] = this.args;
              const n = [...active.values()].filter((row) => row.ipKey === limitIpKey && row.lastSeen >= cutoff).length;
              if (n >= limit) return { success: true, meta: { changes: 0 } };
            }
            active.set(visitorId, { lastSeen, ipKey });
            return { success: true, meta: { changes: 1 } };
          }
          if (sql.includes('DELETE FROM presence_sessions')) {
            const cutoff = this.args[0];
            for (const [id, row] of active) if (row.lastSeen < cutoff) active.delete(id);
          }
          return { success: true, meta: { changes: 0 } };
        },
        async first() {
          sqlCalls.push({ sql, args: this.args });
          if (sql.includes('WHERE visitor_id = ?')) {
            const row = active.get(this.args[0]);
            return row ? { last_seen: row.lastSeen } : null;
          }
          if (sql.includes('WHERE ip_key = ?')) {
            const [ipKey, cutoff] = this.args;
            return { n: [...active.values()].filter((row) => row.ipKey === ipKey && row.lastSeen >= cutoff).length };
          }
          const cutoff = this.args[0];
          return { online: [...active.values()].filter((row) => row.lastSeen >= cutoff).length };
        },
      };
      return stmt;
    },
  };
}

const requestFor = ({ ip = '203.0.113.8', ua = 'Browser A', claimedId = 'caller-controlled-id', cookie = '' } = {}) => new Request(
  'https://etf.peekabo.cc/api/public/v1/presence',
  {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'cf-connecting-ip': ip,
      'user-agent': ua,
      'accept-language': 'zh-CN',
      ...(cookie ? { cookie } : {}),
    },
    body: JSON.stringify({ visitor_id: claimedId }),
  },
);

const envFor = (db) => ({ DB: db, PRESENCE_SECRET: 'presence-test-secret' });

test('presence ignores caller ids and deduplicates a server-issued identity cookie', async () => {
  const db = createDb();
  const first = await onRequestPost({ request: requestFor({ claimedId: 'first-fake-id' }), env: envFor(db) });
  const cookie = first.headers.get('set-cookie')?.split(';')[0] || '';
  const second = await onRequestPost({ request: requestFor({ claimedId: 'second-fake-id', cookie }), env: envFor(db) });

  assert.equal(first.status, 200);
  assert.match(cookie, /^etf_presence=/);
  assert.match(first.headers.get('set-cookie') || '', /HttpOnly/);
  assert.deepEqual(await second.json(), { online: 1, window_seconds: 120 });
  assert.equal(db.active.size, 1);
  assert.equal(db.active.has('first-fake-id'), false);
});

test('presence caps new browser identities from one IP within ten minutes', async () => {
  const db = createDb();
  for (let i = 0; i < 20; i += 1) {
    const response = await onRequestPost({ request: requestFor({ ua: `Browser ${i}` }), env: envFor(db) });
    assert.equal(response.status, 200);
  }
  const blocked = await onRequestPost({ request: requestFor({ ua: 'Browser overflow' }), env: envFor(db) });

  assert.equal(blocked.status, 429);
  assert.equal(db.active.size, 20);
});

test('presence returns a controlled error when its secret is unavailable', async () => {
  const db = createDb();
  const response = await onRequestPost({ request: requestFor(), env: { DB: db } });

  assert.equal(response.status, 503);
  assert.equal(db.sqlCalls.length, 0);
});
