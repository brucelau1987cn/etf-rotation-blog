const encoder = new TextEncoder();

function bytesToBase64(bytes) {
  let binary = '';
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

export async function hashPassword(password, salt = crypto.getRandomValues(new Uint8Array(16))) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(password), { name: 'PBKDF2' }, false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: 100000, hash: 'SHA-256' }, key, 256);
  return `pbkdf2$100000$${bytesToBase64(salt)}$${bytesToBase64(bits)}`;
}

export async function verifyPassword(password, stored) {
  const [, iterations, saltText, digest] = String(stored || '').split('$');
  if (!iterations || !saltText || !digest) return false;
  const key = await crypto.subtle.importKey('raw', encoder.encode(password), { name: 'PBKDF2' }, false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: base64ToBytes(saltText), iterations: Number(iterations), hash: 'SHA-256' }, key, 256);
  return bytesToBase64(bits) === digest;
}

export async function sha256(value) {
  return bytesToBase64(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
}

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
}

export function getCookie(request, name) {
  const match = request.headers.get('cookie')?.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

export function setSessionCookie(token, maxAge) {
  return `session=${encodeURIComponent(token)}; Max-Age=${maxAge}; Path=/; HttpOnly; Secure; SameSite=Lax`;
}

export function clearSessionCookie() {
  return 'session=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax';
}

export function csvTradeDate(csvText) {
  const match = csvText.match(/(?:^|\n).*?(20\d{2}[./-]\d{2}[./-]\d{2})/);
  return match ? match[1].replaceAll('/', '-').replaceAll('.', '-') : null;
}

export async function requireUser(request, env, roles = []) {
  const token = getCookie(request, 'session');
  if (!token) return { error: json({ error: '请先登录' }, 401) };
  const tokenHash = await sha256(token);
  const row = await env.DB.prepare(`SELECT u.id, u.username, u.role, u.must_change_password, u.enabled, s.expires_at
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token_hash = ? AND s.expires_at > datetime('now') AND u.enabled = 1`).bind(tokenHash).first();
  if (!row || (roles.length && !roles.includes(row.role))) return { error: json({ error: '登录已失效或权限不足' }, 401) };
  return { user: row };
}
