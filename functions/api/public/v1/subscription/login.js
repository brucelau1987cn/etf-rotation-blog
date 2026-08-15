// POST /api/public/v1/subscription/login — 订阅密码登录（方式一）
// Body: { passphrase, device_id? }
// 校验订阅密码（sha256）→ 设备会话（每订阅最多 10 台）→ 签发 cookie
import { sha256Hex, resolveDeviceId, acquireSession, issueSubscriptionCookie, MAX_DEVICES } from '../../../../_lib/subscription-auth.js';

export async function onRequestPost(context) {
  const { request, env } = context;
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
  }
  const passphrase = String(body.passphrase || '').trim();
  if (!passphrase) {
    return Response.json({ ok: false, error: '请输入订阅密码' }, { status: 400 });
  }

  const hash = await sha256Hex(passphrase);
  const sub = await env.DB.prepare(
    `SELECT id, label, expires_at, revoked FROM subscriptions WHERE passphrase_hash = ? LIMIT 1`,
  ).bind(hash).first();

  if (!sub || sub.revoked) {
    return Response.json({ ok: false, error: '密码无效' }, { status: 401 });
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
      device_count: sess.deviceCount, device_limit: MAX_DEVICES, method: 'passphrase',
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Set-Cookie': cookie },
    },
  );
}
