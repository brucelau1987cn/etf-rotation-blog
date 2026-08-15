// GET /api/public/v1/me — 当前登录身份（用户菜单用）
// 返回 kind: 'subscriber' | 'admin' | 'anonymous'，附角色/到期/设备数
import {
  readCookie, verifyToken, SUB_COOKIE, ADMIN_COOKIE, MAX_DEVICES,
} from '../../../_lib/subscription-auth.js';

export async function onRequestGet(context) {
  const { request, env } = context;
  const cookie = request.headers.get('Cookie') || '';

  // 1) 管理员
  const adminToken = readCookie(cookie, ADMIN_COOKIE);
  if (adminToken) {
    const p = await verifyToken(adminToken, env.ADMIN_SECRET || 'dev-admin-secret');
    if (p && p.exp && Date.parse(p.exp) > Date.now()) {
      const role = p.role === 'super_admin' ? 'super_admin' : 'admin';
      return Response.json({ ok: true, kind: 'admin', role, expires_at: p.exp });
    }
  }

  // 2) 订阅用户（含设备数）
  const subToken = readCookie(cookie, SUB_COOKIE);
  if (subToken) {
    const p = await verifyToken(subToken, env.SUBSCRIBE_SECRET || 'dev-secret');
    if (p && p.exp && Date.parse(p.exp) > Date.now() && p.sid) {
      const subId = Number(String(p.sub || '').replace('sub:', ''));
      let label = null, deviceCount = null, isVip = false;
      if (subId) {
        const sub = await env.DB.prepare(
          'SELECT label, username FROM subscriptions WHERE id = ?',
        ).bind(subId).first();
        if (sub) {
          label = sub.label;
          isVip = !!sub.username; // 有用户名账号密码的订阅 = VIP 用户
        }
        const c = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM sub_sessions WHERE subscription_id = ? AND expires_at > datetime('now')`,
        ).bind(subId).first();
        deviceCount = Number(c?.n || 0);
      }
      return Response.json({
        ok: true, kind: 'subscriber', label, vip: isVip, expires_at: p.exp,
        device_count: deviceCount, device_limit: MAX_DEVICES,
      });
    }
  }

  return Response.json({ ok: true, kind: 'anonymous' });
}
