// POST /api/public/v1/login-account — 统一账号登录（管理员 + 订阅用户）
// Body: { username, password, device_id? }
// 1) 先查 admin_credentials（管理员：super_admin / admin）→ 发 etf_admin cookie
// 2) 再查 subscriptions（订阅用户）→ 设备会话 + etf_sub cookie
// 前端按响应 kind 跳转：admin → /admin/，subscriber → 原页面
import {
  sha256Hex, signToken, setCookie, ADMIN_COOKIE,
  resolveDeviceId, acquireSession, issueSubscriptionCookie, MAX_DEVICES,
} from '../../../_lib/subscription-auth.js';

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

  const hash = await sha256Hex(`${username}:${password}`);

  // ── 1) 管理员（admin_credentials 表）──
  const adminRow = await env.DB.prepare(
    'SELECT id, username, password_hash, role FROM admin_credentials WHERE username = ? LIMIT 1',
  ).bind(username).first();
  if (adminRow && adminRow.password_hash === hash) {
    const role = adminRow.role === 'admin' ? 'admin' : 'super_admin';
    const remember = body.remember === true;
    // 管理员无期限限制：remember 登录会话长期有效（2099），不勾选则 12 小时
    const ttlSec = remember ? 365 * 24 * 3600 : 12 * 3600;
    const exp = remember
      ? '2099-12-31T00:00:00.000Z'
      : new Date(Date.now() + ttlSec * 1000).toISOString();
    const token = await signToken({ role, exp, sub: `admin:${adminRow.id}` }, env.ADMIN_SECRET || 'dev-admin-secret');
    return new Response(
      JSON.stringify({ ok: true, kind: 'admin', role, expires_at: exp, remember }),
      {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Set-Cookie': setCookie(ADMIN_COOKIE, token, { maxAge: ttlSec, path: '/' }),
        },
      },
    );
  }

  // ── 2) 订阅用户（subscriptions 表，用户名+密码）──
  const sub = await env.DB.prepare(
    'SELECT id, label, expires_at, revoked, password_hash FROM subscriptions WHERE username = ? LIMIT 1',
  ).bind(username).first();
  if (sub && !sub.revoked && sub.password_hash && sub.password_hash === hash) {
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
        ok: true, kind: 'subscriber', label: sub.label, expires_at: sub.expires_at,
        device_count: sess.deviceCount, device_limit: MAX_DEVICES,
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'Set-Cookie': cookie },
      },
    );
  }

  // ── 3) 都不是 → 用户名或密码错误 ──
  return Response.json({ ok: false, error: '用户名或密码错误' }, { status: 401 });
}
