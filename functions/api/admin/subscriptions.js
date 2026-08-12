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

const PERMANENT_DAYS = 9999;
const PERMANENT_EXPIRY = '2099-12-31T00:00:00.000Z';

function daysAhead(days) {
  if (days >= PERMANENT_DAYS) return PERMANENT_EXPIRY;
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

  // 删除订阅（物理删除，不可恢复；设备会话一并清除）
  if (action === 'delete' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    await env.DB.prepare('DELETE FROM sub_sessions WHERE subscription_id = ?').bind(id).run();
    const result = await env.DB.prepare('DELETE FROM subscriptions WHERE id = ?').bind(id).run();
    const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
    if (changes === 0) return Response.json({ ok: false, error: '订阅不存在' }, { status: 404 });
    return Response.json({ ok: true });
  }

  // 启用订阅（撤销的反操作：revoked 1 → 0，恢复登录）
  if (action === 'enable' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    const result = await env.DB.prepare('UPDATE subscriptions SET revoked = 0 WHERE id = ?').bind(id).run();
    const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
    if (changes === 0) return Response.json({ ok: false, error: '订阅不存在' }, { status: 404 });
    return Response.json({ ok: true });
  }

  // 重置密码：单密码授权 → 新随机 passphrase；VIP 账号 → 新随机密码
  // （同步清空设备会话，旧凭据立即失效，新凭据明文仅返回一次）
  if (action === 'reset-password' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    const row = await env.DB.prepare('SELECT id, label, username FROM subscriptions WHERE id = ?').bind(id).first();
    if (!row) return Response.json({ ok: false, error: '订阅不存在' }, { status: 404 });
    if (row.revoked) return Response.json({ ok: false, error: '订阅已停用，请先启用' }, { status: 400 });

    const newPassword = randomPassphrase();
    if (row.username) {
      // VIP 账号：密码 hash = sha256(username:password)
      const hash = await sha256Hex(`${row.username}:${newPassword}`);
      await env.DB.prepare('UPDATE subscriptions SET password_hash = ? WHERE id = ?').bind(hash, id).run();
    } else {
      const hash = await sha256Hex(newPassword);
      await env.DB.prepare('UPDATE subscriptions SET passphrase_hash = ? WHERE id = ?').bind(hash, id).run();
    }
    // 旧设备会话全部失效，需用新密码重新登录
    await env.DB.prepare('DELETE FROM sub_sessions WHERE subscription_id = ?').bind(id).run();
    return Response.json({ ok: true, username: row.username || null, new_password: newPassword, label: row.label });
  }

  // 手动编辑到期时间（body: { id, expires_at: 'YYYY-MM-DD' }）
  if (action === 'update-expiry' && request.method === 'POST') {
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ ok: false, error: '无效请求' }, { status: 400 });
    }
    const id = Number(body.id);
    if (!id) return Response.json({ ok: false, error: '缺少 id' }, { status: 400 });
    const raw = String(body.expires_at || '').trim();
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return Response.json({ ok: false, error: '日期格式应为 YYYY-MM-DD' }, { status: 400 });
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return Response.json({ ok: false, error: '日期不合法' }, { status: 400 });
    const iso = `${m[1]}-${m[2]}-${m[3]}T00:00:00.000Z`;
    const result = await env.DB.prepare('UPDATE subscriptions SET expires_at = ? WHERE id = ?').bind(iso, id).run();
    const changes = Number(result?.meta?.changes ?? result?.changes ?? 0);
    if (changes === 0) return Response.json({ ok: false, error: '订阅不存在' }, { status: 404 });
    return Response.json({ ok: true, expires_at: raw });
  }

  if (request.method === 'GET') {
    const rows = await env.DB.prepare(
      `SELECT id, label, username, expires_at, revoked, created_at FROM subscriptions ORDER BY revoked ASC, expires_at ASC`,
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
    const username = String(body.username || '').trim();
    const passphrase = randomPassphrase();
    const hash = await sha256Hex(passphrase);
    const expiresAt = daysAhead(days);

    // 可选绑定用户名+密码：password_hash 存 sha256(username:password)（与管理员同方案）
    let accountHash = null;
    if (username) {
      const accountPassword = String(body.account_password || '');
      if (accountPassword.length < 8) {
        return Response.json({ ok: false, error: '账号密码至少 8 个字符' }, { status: 400 });
      }
      accountHash = await sha256Hex(`${username}:${accountPassword}`);
    }

    try {
      await env.DB.prepare(
        'INSERT INTO subscriptions (passphrase_hash, label, expires_at, username, password_hash) VALUES (?, ?, ?, ?, ?)',
      ).bind(hash, label, expiresAt, username || null, accountHash).run();
    } catch (e) {
      if (String(e?.message || '').includes('UNIQUE')) {
        return Response.json({ ok: false, error: '用户名已存在' }, { status: 409 });
      }
      throw e;
    }
    return Response.json({
      ok: true, passphrase, label, expires_at: fmtDate(expiresAt), days,
      username: username || null,
    });
  }

  return Response.json({ ok: false, error: '不支持的方法' }, { status: 405 });
}
