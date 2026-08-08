// GET /api/public/v1/subscription/status
// 返回当前订阅登录态 + 绑定设备数（供登录页/页面 JS 判断）
import { isSubscribed, readCookie, verifyToken, SUB_COOKIE } from '../../../../_lib/subscription-auth.js';

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!(await isSubscribed(request, env))) {
    return Response.json({ ok: false, subscribed: false }, { status: 200 });
  }
  const token = readCookie(request.headers.get('Cookie'), SUB_COOKIE);
  const payload = await verifyToken(token, env.SUBSCRIBE_SECRET || 'dev-secret');
  const subId = payload?.sub ? Number(String(payload.sub).replace('sub:', '')) : null;
  let deviceCount = null;
  if (subId) {
    const row = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM sub_sessions WHERE subscription_id = ? AND expires_at > datetime('now')`,
    ).bind(subId).first();
    deviceCount = Number(row?.n || 0);
  }
  return Response.json({
    ok: true, subscribed: true, expires_at: payload?.exp || null,
    device_count: deviceCount, device_limit: 5,
  });
}
