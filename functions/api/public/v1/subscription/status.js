// GET /api/public/v1/subscription/status
// 返回当前订阅登录态（供登录页/页面 JS 判断）
import { isSubscribed, readCookie, verifyToken, SUB_COOKIE } from '../../../../_lib/subscription-auth.js';

export async function onRequestGet(context) {
  const { request, env } = context;
  if (!(await isSubscribed(request, env))) {
    return Response.json({ ok: false, subscribed: false }, { status: 200 });
  }
  const token = readCookie(request.headers.get('Cookie'), SUB_COOKIE);
  const payload = await verifyToken(token, env.SUBSCRIBE_SECRET || 'dev-secret');
  return Response.json({ ok: true, subscribed: true, expires_at: payload?.exp || null });
}
