import test from 'node:test';
import assert from 'node:assert/strict';

import { readFile } from 'node:fs/promises';
import { onRequestPost } from '../functions/api/public/v1/register.js';

class InviteDb {
  constructor({ usedCount = 0, maxUses = 18, expiresAt = '2099-01-01T00:00:00.000Z', revoked = 0, existing = false, batchResults = null } = {}) {
    this.calls = [];
    this.invite = { id: 7, owner_admin_id: 1, expires_at: expiresAt, max_uses: maxUses, used_count: usedCount, revoked };
    this.existing = existing;
    this.batchResults = batchResults;
  }
  prepare(sql) {
    const db = this;
    const stmt = {
      sql,
      args: [],
      bind(...args) { stmt.args = args; return stmt; },
      async first() {
        db.calls.push({ op: 'first', sql, args: stmt.args });
        if (/SELECT id, owner_admin_id/.test(sql)) return db.invite;
        if (/SELECT id FROM subscriptions WHERE username/.test(sql)) return db.existing ? { id: 99 } : null;
        return null;
      },
    };
    return stmt;
  }
  async batch(statements) {
    this.calls.push({ op: 'batch', statements: statements.map((s) => ({ sql: s.sql, args: s.args })) });
    return this.batchResults || [{ meta: { changes: 1 } }, { meta: { changes: 1, last_row_id: 42 } }, { meta: { changes: 1 } }];
  }
}

test('valid invite creates one permanent VIP owned by super admin', async () => {
  const db = new InviteDb();
  const response = await onRequestPost({
    request: new Request('https://example.test/api/public/v1/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: 'valid-invite-code', username: 'new_vip', password: 'strongpass88' }),
    }),
    env: { DB: db },
  });
  assert.equal(response.status, 201);
  const payload = await response.json();
  assert.equal(payload.ok, true);
  assert.equal(payload.expires_at, '2099-12-31T00:00:00.000Z');
  assert.equal('passphrase' in payload, false);
  const batch = db.calls.find((call) => call.op === 'batch');
  assert.ok(batch);
  const insert = batch.statements.find((s) => /INSERT INTO subscriptions/.test(s.sql));
  assert.ok(insert);
  assert.match(insert.sql, /created_by_admin_id/);
  assert.match(insert.sql, /SELECT[\s\S]*owner_admin_id/);
  assert.ok(insert.args.includes('2099-12-31T00:00:00.000Z'));
});

async function registerWith(db, overrides = {}) {
  return onRequestPost({
    request: new Request('https://example.test/api/public/v1/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: 'valid-invite-code', username: 'new_vip', password: 'strongpass88', ...overrides }),
    }),
    env: { DB: db },
  });
}

test('full invite rejects the nineteenth registration before any write', async () => {
  const db = new InviteDb({ usedCount: 18 });
  const response = await registerWith(db);
  assert.equal(response.status, 409);
  assert.equal(db.calls.some((call) => call.op === 'batch'), false);
});

test('expired seven-day invite returns gone', async () => {
  const db = new InviteDb({ expiresAt: '2020-01-01T00:00:00.000Z' });
  const response = await registerWith(db);
  assert.equal(response.status, 410);
});

test('existing username does not consume an invite slot', async () => {
  const db = new InviteDb({ existing: true });
  const response = await registerWith(db);
  assert.equal(response.status, 409);
  assert.equal(db.calls.some((call) => call.op === 'batch'), false);
});

test('failed atomic claim creates no account', async () => {
  const db = new InviteDb({ batchResults: [{ meta: { changes: 0 } }, { meta: { changes: 0 } }, { meta: { changes: 0 } }] });
  const response = await registerWith(db);
  assert.equal(response.status, 409);
  const batch = db.calls.find((call) => call.op === 'batch');
  assert.match(batch.statements[0].sql, /used_count\s*<\s*max_uses/);
  assert.match(batch.statements[0].sql, /last_claim_nonce/);
});

test('registration page collects account credentials and auto logs in', async () => {
  const page = await readFile(new URL('../src/pages/register.astro', import.meta.url), 'utf8');
  assert.match(page, /id="invite-code"/);
  assert.match(page, /id="register-username"/);
  assert.match(page, /id="register-password"/);
  assert.match(page, /id="register-confirm"/);
  assert.match(page, /\/api\/public\/v1\/register/);
  assert.match(page, /\/api\/public\/v1\/login-account/);
  assert.match(page, /history\.replaceState/);
  const headers = await readFile(new URL('../public/_headers', import.meta.url), 'utf8');
  assert.match(headers, /\/register\/\*/);
  assert.match(headers, /Referrer-Policy:\s*no-referrer/);
  assert.match(page, /永久有效/);
  assert.match(page, /18/);
});

test('registration migration enforces bounded invite and unique account audit', async () => {
  const sql = await readFile(new URL('../migrations/0018_invite_registration.sql', import.meta.url), 'utf8');
  assert.match(sql, /max_uses INTEGER NOT NULL/);
  assert.match(sql, /used_count INTEGER NOT NULL DEFAULT 0/);
  assert.match(sql, /last_claim_nonce TEXT/);
  assert.match(sql, /subscription_id INTEGER NOT NULL UNIQUE/);
  assert.match(sql, /username TEXT NOT NULL UNIQUE/);
});
