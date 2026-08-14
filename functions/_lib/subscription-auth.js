// Shared auth helpers for subscription-based site access.
// Token: base64url(payload).base64url(hmac) — payload { exp, sub }
// 密码存 sha256 哈希，不存明文。

export const SUB_COOKIE = 'etf_sub';
export const ADMIN_COOKIE = 'etf_admin';

export async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function hmacHex(secret, payload) {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(payload));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function b64url(s) {
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function unb64url(s) {
  return atob(s.replace(/-/g, '+').replace(/_/g, '/'));
}

// 签发 token：payload 对象 + secret，返回 "payloadB64.sigHex"
export async function signToken(payload, secret) {
  const body = b64url(JSON.stringify(payload));
  const sig = await hmacHex(secret, body);
  return `${body}.${sig}`;
}

// 验证 token：返回 payload 或 null
export async function verifyToken(token, secret) {
  if (!token || typeof token !== 'string') return null;
  const dot = token.lastIndexOf('.');
  if (dot <= 0) return null;
  const body = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expect = await hmacHex(secret, body);
  if (sig.length !== expect.length || sig !== expect) return null;
  try {
    return JSON.parse(unb64url(body));
  } catch {
    return null;
  }
}

// 从 Cookie 头解析指定 cookie
export function readCookie(header, name) {
  if (!header) return null;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq > 0 && part.slice(0, eq).trim() === name) {
      return part.slice(eq + 1).trim();
    }
  }
  return null;
}

export function setCookie(name, value, opts = {}) {
  const parts = [`${name}=${value}`];
  if (opts.maxAge) parts.push(`Max-Age=${opts.maxAge}`);
  if (opts.path) parts.push(`Path=${opts.path}`);
  parts.push('HttpOnly');
  parts.push('SameSite=Lax');
  if (opts.secure) parts.push('Secure');
  return parts.join('; ');
}

// 订阅登录态检查：cookie token 有效、未过期，且 sid 在 sub_sessions 中存在（解绑/超限即时生效）
export async function isSubscribed(request, env) {
  const token = readCookie(request.headers.get('Cookie'), SUB_COOKIE);
  if (!token) return false;
  const payload = await verifyToken(token, env.SUBSCRIBE_SECRET || 'dev-secret');
  if (!payload || !payload.exp) return false;
  if (Date.parse(payload.exp) <= Date.now()) return false;
  if (!payload.sid) return false;
  // 会话必须在 DB 中存在且未过期（设备被解绑或会话失效 → 拒绝）
  const row = await env.DB.prepare(
    'SELECT 1 FROM sub_sessions WHERE sid = ? AND expires_at > datetime(\'now\') LIMIT 1',
  ).bind(payload.sid).first();
  return !!row;
}

// ── 设备会话管理（订阅登录共用）────────────────────────────
export const MAX_DEVICES = 5;

export function genSid() {
  const arr = new Uint8Array(16);
  crypto.getRandomValues(arr);
  return [...arr].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// 解析设备指纹：前端持久 device_id 优先，缺失用 UA 哈希兜底
// body 为已解析的请求体（避免 body 已消费后 clone().json() 抛错）
export async function resolveDeviceId(request, body = {}) {
  const ua = String(request.headers.get('User-Agent') || '');
  const bodyDevice = String(body?.device_id || '').trim();
  if (bodyDevice) return { deviceId: bodyDevice, ua };
  return { deviceId: (await sha256Hex(`ua:${ua}`)).slice(0, 24), ua };
}

// 设备会话处理：同设备复用 / 新设备限额 / 返回 { sid, isNew, deviceCount }
export async function acquireSession(env, subscriptionId, expiresAt, deviceId, ua) {
  const existing = await env.DB.prepare(
    'SELECT sid FROM sub_sessions WHERE subscription_id = ? AND device_id = ? LIMIT 1',
  ).bind(subscriptionId, deviceId).first();

  if (existing) {
    await env.DB.prepare(
      "UPDATE sub_sessions SET expires_at = ?, last_seen = datetime('now') WHERE sid = ?",
    ).bind(expiresAt, existing.sid).run();
    return { sid: existing.sid, isNew: false, deviceCount: null };
  }

  const countRow = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM sub_sessions WHERE subscription_id = ? AND expires_at > datetime('now')`,
  ).bind(subscriptionId).first();
  const deviceCount = Number(countRow?.n || 0);
  if (deviceCount >= MAX_DEVICES) {
    return { limitHit: true, deviceCount };
  }

  const sid = genSid();
  await env.DB.prepare(
    'INSERT INTO sub_sessions (subscription_id, device_id, device_ua, sid, expires_at, last_seen) VALUES (?, ?, ?, ?, ?, datetime(\'now\'))',
  ).bind(subscriptionId, deviceId, ua.slice(0, 200), sid, expiresAt).run();
  return { sid, isNew: true, deviceCount: deviceCount + 1 };
}

// 签发订阅 cookie（exp 对齐订阅到期）
export async function issueSubscriptionCookie(env, subId, sid, expiresAt) {
  const token = await signToken({ sub: `sub:${subId}`, sid, exp: expiresAt }, env.SUBSCRIBE_SECRET || 'dev-secret');
  const maxAge = Math.max(60, Math.floor((Date.parse(expiresAt) - Date.now()) / 1000));
  return setCookie(SUB_COOKIE, token, { maxAge, path: '/' });
}

// 解析管理员身份；普通管理员必须携带 admin:<id>，用于数据归属隔离。
export async function getAdminIdentity(request, env) {
  const token = readCookie(request.headers.get('Cookie'), ADMIN_COOKIE);
  if (!token) return null;
  const payload = await verifyToken(token, env.ADMIN_SECRET || 'dev-admin-secret');
  if (!payload || !['admin', 'super_admin'].includes(payload.role) || !payload.exp) return null;
  if (Date.parse(payload.exp) <= Date.now()) return null;
  const match = String(payload.sub || '').match(/^admin:(\d+)$/);
  const adminId = match ? Number(match[1]) : (payload.role === 'super_admin' ? 1 : null);
  return { role: payload.role, adminId };
}

// 管理员登录态检查（super_admin 或 admin 都算管理员）
export async function isAdmin(request, env) {
  return !!(await getAdminIdentity(request, env));
}

// 超级管理员检查（仅 brucelau1987）
export async function isSuperAdmin(request, env) {
  const identity = await getAdminIdentity(request, env);
  return identity?.role === 'super_admin';
}
