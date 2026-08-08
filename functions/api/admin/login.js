// POST /api/admin/login
// Body: { password } — 校验管理员密码（env.ADMIN_PASSWORD）→ 发管理员 cookie
import { signToken, setCookie, ADMIN_COOKIE } from '../../_lib/subscription-auth.js';

export async function onRequestPost(context) {
  const { request, env } = context;
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
  }
  const password = String(body.password || '');
  const expected = env.ADMIN_PASSWORD || '';
  if (!expected || password !== expected) {
    return Response.json({ ok: false, error: '管理员密码错误' }, { status: 401 });
  }

  const exp = new Date(Date.now() + 12 * 3600 * 1000).toISOString(); // 12h 管理会话
  const token = await signToken({ role: 'admin', exp }, env.ADMIN_SECRET || 'dev-admin-secret');
  return new Response(
    JSON.stringify({ ok: true, expires_at: exp }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Set-Cookie': setCookie(ADMIN_COOKIE, token, { maxAge: 12 * 3600, path: '/' }),
      },
    },
  );
}
