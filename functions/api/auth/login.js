import { verifyPassword, json, sha256, setSessionCookie } from '../../_lib/auth.js';

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return json({ error: '请求格式错误' }, 400); }
  const username = String(body?.username || '').trim().toLowerCase();
  const password = String(body?.password || '');
  if (!username || password.length < 8) return json({ error: '请输入用户名和至少8位密码' }, 400);
  const user = await env.DB.prepare('SELECT id, username, role, password_hash, must_change_password, enabled FROM users WHERE username = ?').bind(username).first();
  if (!user || !user.enabled || !(await verifyPassword(password, user.password_hash))) return json({ error: '用户名或密码错误' }, 401);
  const token = crypto.randomUUID() + crypto.randomUUID();
  const tokenHash = await (await import('../../_lib/auth.js')).sha256(token);
  const ttl = Math.max(1, Number(env.SESSION_TTL_DAYS || 14));
  await env.DB.batch([
    env.DB.prepare('DELETE FROM sessions WHERE user_id = ? OR expires_at <= datetime(\'now\')').bind(user.id),
    env.DB.prepare('INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (?, ?, datetime(\'now\', ?))').bind(user.id, tokenHash, `+${ttl} days`),
    env.DB.prepare('UPDATE users SET last_login_at = datetime(\'now\') WHERE id = ?').bind(user.id),
  ]);
  return new Response(JSON.stringify({ user: { username: user.username, role: user.role, must_change_password: Boolean(user.must_change_password) } }), { headers: { 'content-type': 'application/json', 'cache-control': 'no-store', 'set-cookie': setSessionCookie(token, ttl * 86400) } });
}
