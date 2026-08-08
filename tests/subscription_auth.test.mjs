import { describe, it } from 'node:test';
import assert from 'node:assert';
import {
  sha256Hex, signToken, verifyToken, readCookie, isSubscribed, isAdmin,
} from '../functions/_lib/subscription-auth.js';

describe('subscription-auth lib', () => {
  it('sha256Hex deterministic', async () => {
    const a = await sha256Hex('abc');
    assert.equal(a.length, 64);
    assert.equal(a, await sha256Hex('abc'));
    assert.notEqual(a, await sha256Hex('abd'));
  });

  it('signToken/verifyToken roundtrip + tamper detection', async () => {
    const secret = 'test-secret';
    const token = await signToken({ sub: 'sub:1', exp: '2026-09-01T00:00:00.000Z' }, secret);
    assert.ok(token.includes('.'));
    const payload = await verifyToken(token, secret);
    assert.equal(payload.sub, 'sub:1');
    assert.equal(payload.exp, '2026-09-01T00:00:00.000Z');
    // 篡改 payload → 验证失败
    const [body] = token.split('.');
    const tampered = `${body}.deadbeef`;
    assert.equal(await verifyToken(tampered, secret), null);
    // 错误 secret → 验证失败
    assert.equal(await verifyToken(token, 'wrong-secret'), null);
  });

  it('readCookie parses headers', () => {
    assert.equal(readCookie('a=1; etf_sub=abc123; b=2', 'etf_sub'), 'abc123');
    assert.equal(readCookie('etf_sub=xyz', 'etf_sub'), 'xyz');
    assert.equal(readCookie('a=1', 'etf_sub'), null);
    assert.equal(readCookie(null, 'etf_sub'), null);
  });

  it('isSubscribed honors expiry + sid existence', async () => {
    const secret = 'test-secret';
    const mkToken = (expIso) => signToken({ exp: expIso, sid: 'sid-test' }, secret);
    const future = await mkToken(new Date(Date.now() + 3600e3).toISOString());
    const past = await mkToken(new Date(Date.now() - 3600e3).toISOString());
    // mock DB：会话存在 → isSubscribed true；不存在 → false
    const envWithDb = (exists) => ({
      SUBSCRIBE_SECRET: secret,
      DB: { prepare: () => ({ bind: () => ({ first: async () => (exists ? { 1: 1 } : null) }) }) },
    });
    const headers = (cookie) => ({ headers: new Headers({ Cookie: `etf_sub=${cookie}` }) });
    assert.equal(await isSubscribed(headers(future), envWithDb(true)), true);
    assert.equal(await isSubscribed(headers(future), envWithDb(false)), false); // 会话被删 → 拒
    assert.equal(await isSubscribed(headers(past), envWithDb(true)), false); // 过期 → 拒
    assert.equal(await isSubscribed({ headers: new Headers() }, envWithDb(true)), false);
  });

  it('isAdmin checks role', async () => {
    const secret = 'admin-test';
    const ok = await signToken({ role: 'admin', exp: new Date(Date.now() + 3600e3).toISOString() }, secret);
    const wrongRole = await signToken({ role: 'user', exp: new Date(Date.now() + 3600e3).toISOString() }, secret);
    const env = { ADMIN_SECRET: secret };
    assert.equal(await isAdmin({ headers: new Headers({ Cookie: `etf_admin=${ok}` }) }, env), true);
    assert.equal(await isAdmin({ headers: new Headers({ Cookie: `etf_admin=${wrongRole}` }) }, env), false);
    assert.equal(await isAdmin({ headers: new Headers() }, env), false);
  });
});
