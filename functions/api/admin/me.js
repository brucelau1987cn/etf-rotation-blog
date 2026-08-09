// GET /api/admin/me — 返回当前管理员信息（角色等）
import { readCookie, verifyToken, ADMIN_COOKIE } from '../../_lib/subscription-auth.js';

export async function onRequestGet(context) {
  const { request, env } = context;
  const token = readCookie(request.headers.get('Cookie'), ADMIN_COOKIE);
  if (!token) {
    return Response.json({ ok: false, error: '未登录' }, { status: 401 });
  }
  const payload = await verifyToken(token, env.ADMIN_SECRET || 'dev-admin-secret');
  if (!payload || !payload.exp || Date.parse(payload.exp) <= Date.now()) {
    return Response.json({ ok: false, error: '会话过期' }, { status: 401 });
  }
  const role = payload.role === 'super_admin' ? 'super_admin' : 'admin';
  return Response.json({ ok: true, role, expires_at: payload.exp });
}
