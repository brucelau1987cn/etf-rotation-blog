import { hashPassword, json, requireUser, verifyPassword } from '../../_lib/auth.js';

export async function onRequestPost({ request, env }) {
  const result = await requireUser(request, env);
  if (result.error) return result.error;
  let body;
  try { body = await request.json(); } catch { return json({ error: '请求格式错误' }, 400); }
  const oldPassword = String(body?.old_password || '');
  const newPassword = String(body?.new_password || '');
  if (newPassword.length < 10) return json({ error: '新密码至少需要10位' }, 400);
  const row = await env.DB.prepare('SELECT password_hash FROM users WHERE id = ?').bind(result.user.id).first();
  if (!(await verifyPassword(oldPassword, row?.password_hash))) return json({ error: '当前密码错误' }, 400);
  await env.DB.prepare('UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?').bind(await hashPassword(newPassword), result.user.id).run();
  return json({ ok: true });
}
