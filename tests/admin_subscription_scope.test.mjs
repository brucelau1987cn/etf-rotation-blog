import test from 'node:test';
import assert from 'node:assert/strict';

import { onRequest } from '../functions/api/admin/subscriptions.js';
import { onRequestPost as adminLogin } from '../functions/api/admin/login.js';
import { readCookie, signToken, verifyToken } from '../functions/_lib/subscription-auth.js';

const SECRET = 'scope-test-secret';

async function adminRequest(role, adminId, method = 'GET', url = 'https://example.test/api/admin/subscriptions', body) {
  const token = await signToken({
    role,
    sub: `admin:${adminId}`,
    exp: new Date(Date.now() + 3600_000).toISOString(),
  }, SECRET);
  return new Request(url, {
    method,
    headers: {
      Cookie: `etf_admin=${token}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

class ScopeDb {
  constructor() {
    this.subscriptions = [
      { id: 11, label: 'super-created', username: 'vip11', expires_at: '2099-12-31T00:00:00.000Z', revoked: 0, created_at: '2026-08-10', created_by_admin_id: 1 },
      { id: 22, label: 'admin-created', username: 'vip22', expires_at: '2099-12-31T00:00:00.000Z', revoked: 0, created_at: '2026-08-11', created_by_admin_id: 2 },
    ];
    this.calls = [];
  }

  prepare(sql) {
    const db = this;
    const stmt = {
      args: [],
      bind(...args) { stmt.args = args; return stmt; },
      async all() {
        db.calls.push({ op: 'all', sql, args: stmt.args });
        const owner = /created_by_admin_id\s*=\s*\?/.test(sql) ? Number(stmt.args[0]) : null;
        return { results: owner == null ? db.subscriptions : db.subscriptions.filter((row) => row.created_by_admin_id === owner) };
      },
      async first() {
        db.calls.push({ op: 'first', sql, args: stmt.args });
        if (/COUNT\(\*\)/.test(sql)) return { n: 0 };
        if (/SELECT id FROM subscriptions/.test(sql)) {
          const [id, owner] = stmt.args.map(Number);
          return db.subscriptions.find((row) => row.id === id && (owner === undefined || row.created_by_admin_id === owner)) || null;
        }
        return null;
      },
      async run() {
        db.calls.push({ op: 'run', sql, args: stmt.args });
        return { meta: { changes: 1 } };
      },
    };
    return stmt;
  }
}

test('ordinary admin lists only subscriptions created by that admin', async () => {
  const db = new ScopeDb();
  const response = await onRequest({
    request: await adminRequest('admin', 2),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.deepEqual(payload.items.map((item) => item.id), [22]);
  const listCall = db.calls.find((call) => call.op === 'all');
  assert.match(listCall.sql, /created_by_admin_id\s*=\s*\?/);
  assert.deepEqual(listCall.args, [2]);
});

test('new subscription records the creating admin identity', async () => {
  const db = new ScopeDb();
  const response = await onRequest({
    request: await adminRequest('admin', 2, 'POST', 'https://example.test/api/admin/subscriptions', {
      label: 'owned by admin 2',
      days: 30,
    }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const insert = db.calls.find((call) => call.op === 'run' && /INSERT INTO subscriptions/.test(call.sql));
  assert.ok(insert);
  assert.match(insert.sql, /created_by_admin_id/);
  assert.equal(insert.args.at(-1), 2);
});

test('permanent subscription bypasses finite-day clamping', async () => {
  const db = new ScopeDb();
  const response = await onRequest({
    request: await adminRequest('super_admin', 1, 'POST', 'https://example.test/api/admin/subscriptions', {
      label: 'permanent VIP',
      username: 'long',
      account_password: 'long-password',
      days: 9999,
    }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.days, 9999);
  assert.equal(payload.expires_at, '2099-12-31');
  const insert = db.calls.find((call) => call.op === 'run' && /INSERT INTO subscriptions/.test(call.sql));
  assert.equal(insert.args[2], '2099-12-31T00:00:00.000Z');
});

test('only the exact permanent sentinel bypasses finite-day clamping', async () => {
  for (const requestedDays of [3651, 9998, 10000]) {
    const db = new ScopeDb();
    const response = await onRequest({
      request: await adminRequest('super_admin', 1, 'POST', 'https://example.test/api/admin/subscriptions', {
        label: `finite VIP ${requestedDays}`,
        username: `finite-${requestedDays}`,
        account_password: 'finite-password',
        days: requestedDays,
      }),
      env: { DB: db, ADMIN_SECRET: SECRET },
    });
    assert.equal(response.status, 200);
    const payload = await response.json();
    assert.equal(payload.days, 3650);
    assert.notEqual(payload.expires_at, '2099-12-31');
    const insert = db.calls.find((call) => call.op === 'run' && /INSERT INTO subscriptions/.test(call.sql));
    assert.notEqual(insert.args[2], '2099-12-31T00:00:00.000Z');
  }
});

test('ordinary admin cannot mutate a super-admin subscription by id', async () => {
  const db = new ScopeDb();
  const response = await onRequest({
    request: await adminRequest('admin', 2, 'POST', 'https://example.test/api/admin/subscriptions?action=revoke', { id: 11 }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 404);
  assert.equal(db.calls.some((call) => call.op === 'run'), false);
});

test('direct admin login token carries the admin id used for ownership scope', async () => {
  const password = 'scope-password';
  const { sha256Hex } = await import('../functions/_lib/subscription-auth.js');
  const passwordHash = await sha256Hex(`manager:${password}`);
  const db = {
    prepare() {
      return {
        bind() { return this; },
        async first() { return { id: 2, username: 'manager', password_hash: passwordHash, role: 'admin' }; },
      };
    },
  };
  const response = await adminLogin({
    request: new Request('https://example.test/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'manager', password }),
    }),
    env: { DB: db, ADMIN_SECRET: SECRET },
  });
  assert.equal(response.status, 200);
  const token = readCookie(response.headers.get('Set-Cookie'), 'etf_admin');
  const payload = await verifyToken(token, SECRET);
  assert.equal(payload.sub, 'admin:2');
});
