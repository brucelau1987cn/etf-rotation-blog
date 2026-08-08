// /api/admin/subscriptions — 订阅管理 CRUD（需管理员 cookie）
// GET  → 列表（含每订阅设备数）
// POST → 创建（body: { label, days }）→ 生成随机密码，返回明文一次
// POST ?action=revoke      body: { id }   撤销订阅
// POST ?action=unbind      body: { id }   解绑该订阅全部设备
import { isAdmin, sha256Hex } from '../../_lib/subscription-auth.js';

function randomPassphrase(len = 12) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  const arr = new Uint8Array(len);
  crypto.getRandomValues(arr);
  let s = '';
  for (const b of arr) s += chars[b % chars.length];
  return s;
}

function daysAhead(days) {
  return new Date(Date.now() + days * 86400 * 1000).toISOString();
}

function fmtDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  if (!(await isAdmin(request, env))) {
    return Response.json({ ok: false, error: '未登录或会话过期' }, { status: 401 });
  }

  const action = url.searchParams.get('action');

  // 撤销订阅（设备会话一并删除）
  if (action === 'revoke' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    await env.DB.prepare('UPDATE subscriptions SET revoked = 1 WHERE id = ?').bind(id).run();
    await env.DB.prepare('DELETE FROM sub_sessions WHERE subscription_id = ?').bind(id).run();
    return Response.json({ ok: true });
  }

  // 解绑全部设备（不清除订阅，只踢设备 → 需重新登录）
  if (action === 'unbind' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    await env.DB.prepare('DELETE FROM sub_sessions WHERE subscription_id = ?').bind(id).run();
    return Response.json({ ok: true });
  }

  if (request.method === 'GET') {
    const rows = await env.DB.prepare(
      `SELECT id, label, expires_at, revoked, created_at FROM subscriptions ORDER BY revoked ASC, expires_at ASC`,
    ).all();
    const items = (rows.results || []).map((r) => ({ ...r, expires_at: fmtDate(r.expires_at) }));
    // 每订阅设备数
    for (const it of items) {
      const c = await env.DB.prepare(
        `SELECT COUNT(*) AS n FROM sub_sessions WHERE subscription_id = ? AND expires_at > datetime('now')`,
      ).bind(it.id).first();
      it.device_count = Number(c?.n || 0);
      it.device_limit = 5;
    }
    return Response.json({ ok: true, items });
  }

  if (request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const label = String(body.label || '').trim();
    const days = Math.max(1, Math.min(3650, Number(body.days) || 30));
    const passphrase = randomPassphrase();
    const hash = await sha256Hex(passphrase);
    const expiresAt = daysAhead(days);
    await env.DB.prepare(
      'INSERT INTO subscriptions (passphrase_hash, label, expires_at) VALUES (?, ?, ?)',
    ).bind(hash, label, expiresAt).run();
    return Response.json({ ok: true, passphrase, label, expires_at: fmtDate(expiresAt), days });
  }

  return Response.json({ ok: false, error: '不支持的方法' }, { status: 405 });
}
