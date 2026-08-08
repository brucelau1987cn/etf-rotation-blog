// POST /api/public/v1/subscription/login-account — 用户名+密码登录（方式二）
// Body: { username, password, device_id? }
// 校验订阅用户名+密码（password 哈希 = sha256(username:password)，与管理员同方案）
// → 设备会话（每订阅最多 5 台）→ 签发 cookie
import { sha256Hex, resolveDeviceId, acquireSession, issueSubscriptionCookie, MAX_DEVICES } from '../../../../_lib/subscription-auth.js';

export async function onRequestPost(context) {
  const { request, env } = context;
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
  }
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  if (!username || !password) {
    return Response.json({ ok: false, error: '请输入用户名和密码' }, { status: 400 });
  }

  const sub = await env.DB.prepare(
    'SELECT id, label, expires_at, revoked FROM subscriptions WHERE username = ? LIMIT 1',
  ).bind(username).first();

  if (!sub || sub.revoked) {
    return Response.json({ ok: false, error: '用户名或密码错误' }, { status: 401 });
  }
  const hash = await sha256Hex(`${username}:${password}`);
  const row = await env.DB.prepare(
    'SELECT password_hash FROM subscriptions WHERE id = ?',
  ).bind(sub.id).first();
  if (!row || !row.password_hash || row.password_hash !== hash) {
    return Response.json({ ok: false, error: '用户名或密码错误' }, { status: 401 });
  }

  const expiresMs = Date.parse(sub.expires_at);
  if (Number.isNaN(expiresMs) || expiresMs <= Date.now()) {
    return Response.json({ ok: false, error: `订阅已过期（${sub.expires_at}）` }, { status: 403 });
  }

  const { deviceId, ua } = await resolveDeviceId(request, body);
  const sess = await acquireSession(env, sub.id, sub.expires_at, deviceId, ua);
  if (sess.limitHit) {
    return Response.json({
      ok: false,
      error: `已达设备上限（${MAX_DEVICES} 台）。如需更换设备，请联系管理员在后台解绑。`,
      device_limit: MAX_DEVICES,
    }, { status: 403 });
  }

  const cookie = await issueSubscriptionCookie(env, sub.id, sess.sid, sub.expires_at);
  return new Response(
    JSON.stringify({
      ok: true, label: sub.label, expires_at: sub.expires_at,
      device_count: sess.deviceCount, method: 'account',
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Set-Cookie': cookie },
    },
  );
}
