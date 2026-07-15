import { hashPassword, json, requireUser } from '../../_lib/auth.js';

export async function onRequestGet({ request, env }) {
  const result = await requireUser(request, env, ['admin']);
  if (result.error) return result.error;
  const { results } = await env.DB.prepare('SELECT id, username, role, must_change_password, enabled, created_at, last_login_at FROM users ORDER BY id').all();
  return json({ users: results });
}

export async function onRequestPost({ request, env }) {
  const result = await requireUser(request, env, ['admin']);
  if (result.error) return result.error;
  let body;
  try { body = await request.json(); } catch { return json({ error: '请求格式错误' }, 400); }
  const username = String(body?.username || '').trim().toLowerCase();
  const password = String(body?.password || '');
  const role = body?.role === 'admin' ? 'admin' : 'user';
  if (!/^[a-z0-9][a-z0-9._-]{2,31}$/.test(username)) return json({ error: '用户名需为3-32位字母、数字、点、下划线或短横线' }, 400);
  if (password.length < 10) return json({ error: '初始密码至少需要10位' }, 400);
  try {
    await env.DB.prepare('INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, 1)').bind(username, await hashPassword(password), role).run();
  } catch (error) {
    if (String(error).includes('UNIQUE')) return json({ error: '用户名已存在' }, 409);
    throw error;
  }
  return json({ ok: true, username, role }, 201);
}

export async function onRequestPatch({ request, env }) {
  const result = await requireUser(request, env, ['admin']);
  if (result.error) return result.error;
  let body;
  try { body = await request.json(); } catch { return json({ error: '请求格式错误' }, 400); }
  const id = Number(body?.id);
  if (!Number.isInteger(id) || id <= 0 || id === result.user.id) return json({ error: '无效的用户ID' }, 400);
  if (body?.enabled === false) await env.DB.prepare('UPDATE users SET enabled = 0 WHERE id = ?').bind(id).run();
  else if (body?.enabled === true) await env.DB.prepare('UPDATE users SET enabled = 1 WHERE id = ?').bind(id).run();
  else if (String(body?.password || '').length >= 10) await env.DB.prepare('UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?').bind(await hashPassword(String(body.password)), id).run();
  else return json({ error: '请提供enabled或至少10位新密码' }, 400);
  return json({ ok: true });
}
