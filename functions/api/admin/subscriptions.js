// /api/admin/subscriptions — 订阅管理 CRUD（需管理员 cookie）
// GET  → 列表
// POST → 创建（body: { label, days }）→ 生成随机密码，返回明文一次
// POST /api/admin/subscriptions/:id/revoke → 撤销
import { isAdmin, sha256Hex } from '../../_lib/subscription-auth.js';

function randomPassphrase(len = 12) {
  // 易读字母数字，避免易混淆字符
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

  // POST /api/admin/subscriptions?action=revoke — body: { id }
  if (url.searchParams.get('action') === 'revoke' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    await env.DB.prepare('UPDATE subscriptions SET revoked = 1 WHERE id = ?').bind(id).run();
    return Response.json({ ok: true });
  }

  if (request.method === 'GET') {
    const rows = await env.DB.prepare(
      `SELECT id, label, expires_at, revoked, created_at FROM subscriptions ORDER BY revoked ASC, expires_at ASC`,
    ).all();
    return Response.json({
      ok: true,
      items: (rows.results || []).map((r) => ({ ...r, expires_at: fmtDate(r.expires_at) })),
    });
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
