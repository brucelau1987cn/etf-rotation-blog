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

// 管理员登录态检查
export async function isAdmin(request, env) {
  const token = readCookie(request.headers.get('Cookie'), ADMIN_COOKIE);
  if (!token) return false;
  const payload = await verifyToken(token, env.ADMIN_SECRET || 'dev-admin-secret');
  if (!payload || payload.role !== 'admin' || !payload.exp) return false;
  return Date.parse(payload.exp) > Date.now();
}
