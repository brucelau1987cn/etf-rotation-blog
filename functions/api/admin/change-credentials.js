// POST /api/admin/change-credentials
// Body: { username, password } — 修改管理员用户名/密码（需管理员登录态）
// 改后旧 cookie 立即失效（下次登录用新凭据）
import { isSuperAdmin, sha256Hex } from '../../_lib/subscription-auth.js';

export async function onRequestPost(context) {
  const { request, env } = context;
  if (!(await isSuperAdmin(request, env))) {
    return Response.json({ ok: false, error: '仅超级管理员可修改管理员凭据' }, { status: 403 });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
  }
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  if (username.length < 3 || username.length > 32) {
    return Response.json({ ok: false, error: '用户名需 3-32 个字符' }, { status: 400 });
  }
  if (password.length < 8) {
    return Response.json({ ok: false, error: '密码至少 8 个字符' }, { status: 400 });
  }
  const hash = await sha256Hex(`${username}:${password}`);
  await env.DB.prepare(
    'UPDATE admin_credentials SET username = ?, password_hash = ?, updated_at = datetime(\'now\') WHERE id = 1',
  ).bind(username, hash).run();
  return Response.json({ ok: true, username });
}
