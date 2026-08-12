// POST /api/public/v1/account/change-password — 个人密码修改
// 支持管理员（admin_credentials）与订阅用户（subscriptions：VIP 或单密码授权）
// body: { old_password, new_password }  → 新密码最少 6 位，不限字符类型
import {
  readCookie, verifyToken, SUB_COOKIE, ADMIN_COOKIE, sha256Hex,
} from '../../../../_lib/subscription-auth.js';

export async function onRequestPost(context) {
  const { request, env } = context;
  const cookie = request.headers.get('Cookie') || '';

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
  }
  const oldPassword = String(body.old_password || '');
  const newPassword = String(body.new_password || '');
  if (!oldPassword) return Response.json({ ok: false, error: '请输入旧密码' }, { status: 400 });
  if (newPassword.length < 6) return Response.json({ ok: false, error: '新密码至少 6 位' }, { status: 400 });
  if (newPassword === oldPassword) return Response.json({ ok: false, error: '新密码不能与旧密码相同' }, { status: 400 });

  // ── 1) 管理员 ──
  const adminToken = readCookie(cookie, ADMIN_COOKIE);
  if (adminToken) {
    const p = await verifyToken(adminToken, env.ADMIN_SECRET || 'dev-admin-secret');
    if (p && p.exp && Date.parse(p.exp) > Date.now() && p.sub) {
      const adminId = Number(String(p.sub || '').replace('admin:', ''));
      const row = await env.DB.prepare(
        'SELECT id, username, password_hash FROM admin_credentials WHERE id = ?',
      ).bind(adminId).first();
      if (!row) return Response.json({ ok: false, error: '账号不存在' }, { status: 404 });
      const oldHash = await sha256Hex(`${row.username}:${oldPassword}`);
      if (row.password_hash !== oldHash) {
        return Response.json({ ok: false, error: '旧密码不正确' }, { status: 403 });
      }
      const newHash = await sha256Hex(`${row.username}:${newPassword}`);
      await env.DB.prepare('UPDATE admin_credentials SET password_hash = ? WHERE id = ?').bind(newHash, adminId).run();
      return Response.json({ ok: true, kind: 'admin' });
    }
  }

  // ── 2) 订阅用户 ──
  const subToken = readCookie(cookie, SUB_COOKIE);
  if (subToken) {
    const p = await verifyToken(subToken, env.SUBSCRIBE_SECRET || 'dev-secret');
    if (p && p.exp && Date.parse(p.exp) > Date.now() && p.sid) {
      const subId = Number(String(p.sub || '').replace('sub:', ''));
      const row = await env.DB.prepare(
        'SELECT id, username, password_hash, passphrase_hash, revoked FROM subscriptions WHERE id = ?',
      ).bind(subId).first();
      if (!row) return Response.json({ ok: false, error: '账号不存在' }, { status: 404 });
      if (row.revoked) return Response.json({ ok: false, error: '账号已停用' }, { status: 403 });

      // 旧密码校验：VIP → sha256(username:old)；单密码 → sha256(old)
      let oldOk = false;
      if (row.username && row.password_hash) {
        oldOk = row.password_hash === await sha256Hex(`${row.username}:${oldPassword}`);
      } else if (row.passphrase_hash) {
        oldOk = row.passphrase_hash === await sha256Hex(oldPassword);
      }
      if (!oldOk) return Response.json({ ok: false, error: '旧密码不正确' }, { status: 403 });

      if (row.username) {
        const newHash = await sha256Hex(`${row.username}:${newPassword}`);
        await env.DB.prepare('UPDATE subscriptions SET password_hash = ? WHERE id = ?').bind(newHash, subId).run();
      } else {
        const newHash = await sha256Hex(newPassword);
        await env.DB.prepare('UPDATE subscriptions SET passphrase_hash = ? WHERE id = ?').bind(newHash, subId).run();
      }
      // 清除全部设备会话（含当前），改密后需重新登录
      await env.DB.prepare('DELETE FROM sub_sessions WHERE subscription_id = ?').bind(subId).run();
      return Response.json({ ok: true, kind: 'subscriber' });
    }
  }

  return Response.json({ ok: false, error: '未登录或会话过期' }, { status: 401 });
}
