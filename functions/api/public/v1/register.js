// POST /api/public/v1/register — redeem a bounded invite into a permanent VIP account.
import { sha256Hex } from '../../../_lib/subscription-auth.js';

export const PERMANENT_EXPIRY = '2099-12-31T00:00:00.000Z';

function randomToken(len = 16) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => chars[b % chars.length]).join('');
}

function changes(result) {
  return Number(result?.meta?.changes ?? result?.changes ?? 0);
}

function json(payload, status) {
  return Response.json(payload, {
    status,
    headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
  });
}

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: '无效请求' }, 400); }
  const code = String(body.code || '').trim();
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  if (code.length < 8 || code.length > 128) return json({ ok: false, error: '邀请码无效' }, 400);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$/.test(username)) {
    return json({ ok: false, error: '用户名需为3—32位字母、数字、下划线或连字符' }, 400);
  }
  if (password.length < 8 || password.length > 128) return json({ ok: false, error: '密码需为8—128个字符' }, 400);
  if (!env.DB?.batch) return json({ ok: false, error: '注册服务暂不可用' }, 503);

  const codeHash = await sha256Hex(code);
  const invite = await env.DB.prepare(
    `SELECT id, owner_admin_id, expires_at, max_uses, used_count, revoked
     FROM invite_codes WHERE code_hash = ? LIMIT 1`,
  ).bind(codeHash).first();
  if (!invite || invite.revoked) return json({ ok: false, error: '邀请码无效或已停用' }, 404);
  if (Date.parse(invite.expires_at) <= Date.now()) return json({ ok: false, error: '邀请码已到期' }, 410);
  if (Number(invite.used_count) >= Number(invite.max_uses)) return json({ ok: false, error: '邀请码名额已满' }, 409);

  const existing = await env.DB.prepare('SELECT id FROM subscriptions WHERE username = ? LIMIT 1').bind(username).first();
  if (existing) return json({ ok: false, error: '用户名已存在' }, 409);

  const passphrase = randomToken();
  const passphraseHash = await sha256Hex(passphrase);
  const passwordHash = await sha256Hex(`${username}:${password}`);
  const nonce = randomToken(24);
  const claim = env.DB.prepare(
    `UPDATE invite_codes
     SET used_count = used_count + 1, last_claim_nonce = ?
     WHERE id = ? AND revoked = 0 AND datetime(expires_at) > datetime('now') AND used_count < max_uses`,
  ).bind(nonce, invite.id);
  const create = env.DB.prepare(
    `INSERT INTO subscriptions
       (passphrase_hash, label, expires_at, username, password_hash, created_by_admin_id)
     SELECT ?, ?, ?, ?, ?, owner_admin_id
     FROM invite_codes WHERE id = ? AND last_claim_nonce = ?`,
  ).bind(passphraseHash, username, PERMANENT_EXPIRY, username, passwordHash, invite.id, nonce);
  const audit = env.DB.prepare(
    `INSERT INTO invite_registrations (invite_code_id, subscription_id, username)
     SELECT ?, id, username FROM subscriptions WHERE username = ?`,
  ).bind(invite.id, username);

  let results;
  try {
    results = await env.DB.batch([claim, create, audit]);
  } catch (error) {
    if (String(error?.message || '').includes('UNIQUE')) return json({ ok: false, error: '用户名已存在' }, 409);
    console.error(JSON.stringify({ event: 'invite_registration_error', message: String(error?.message || 'unknown') }));
    return json({ ok: false, error: '注册失败，请稍后重试' }, 500);
  }
  if (changes(results?.[0]) !== 1 || changes(results?.[1]) !== 1 || changes(results?.[2]) !== 1) {
    return json({ ok: false, error: '邀请码已到期或名额已满' }, 409);
  }
  return json({
    ok: true,
    kind: 'subscriber',
    role: 'vip',
    username,
    expires_at: PERMANENT_EXPIRY,
    permanent: true,
    device_limit: 10,
  }, 201);
}
