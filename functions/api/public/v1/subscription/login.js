// POST /api/public/v1/subscription/login
// Body: { passphrase, device_id?, device_ua? }
// 校验订阅密码（D1 subscriptions 表）→ 设备会话管理（每订阅最多 5 台绑定设备）→ 签发 cookie
import { sha256Hex, signToken, setCookie, SUB_COOKIE } from '../../../../_lib/subscription-auth.js';

const MAX_DEVICES = 5; // 每订阅最多绑定设备数

function genSid() {
  const arr = new Uint8Array(16);
  crypto.getRandomValues(arr);
  return [...arr].map((b) => b.toString(16).padStart(2, '0')).join('');
}

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

  // 设备指纹：优先前端持久 device_id，缺失则用 UA 哈希兜底
  const ua = String(request.headers.get('User-Agent') || '');
  let deviceId = String(body.device_id || '').trim();
  if (!deviceId) {
    deviceId = (await sha256Hex(`ua:${ua}`)).slice(0, 24);
  }
  const maxAge = Math.max(60, Math.floor((expiresMs - Date.now()) / 1000));

  // 已有该设备的会话？复用（同一设备重复登录不新增）
  const existing = await env.DB.prepare(
    'SELECT sid FROM sub_sessions WHERE subscription_id = ? AND device_id = ? LIMIT 1',
  ).bind(sub.id, deviceId).first();

  if (existing) {
    // 复用旧 sid，刷新到期时间
    const sid = existing.sid;
    await env.DB.prepare(
      "UPDATE sub_sessions SET expires_at = ?, last_seen = datetime('now') WHERE sid = ?",
    ).bind(sub.expires_at, sid).run();
    const token = await signToken({ sub: `sub:${sub.id}`, sid, exp: sub.expires_at }, env.SUBSCRIBE_SECRET || 'dev-secret');
    return new Response(
      JSON.stringify({ ok: true, label: sub.label, expires_at: sub.expires_at, device_count: null }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'Set-Cookie': setCookie(SUB_COOKIE, token, { maxAge, path: '/' }) },
      },
    );
  }

  // 新设备：检查绑定数量上限
  const countRow = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM sub_sessions WHERE subscription_id = ? AND expires_at > datetime('now')`,
  ).bind(sub.id).first();
  const deviceCount = Number(countRow?.n || 0);
  if (deviceCount >= MAX_DEVICES) {
    return Response.json({
      ok: false,
      error: `已达设备上限（${MAX_DEVICES} 台）。如需更换设备，请联系管理员在后台解绑。`,
      device_limit: MAX_DEVICES,
    }, { status: 403 });
  }

  const sid = genSid();
  await env.DB.prepare(
    'INSERT INTO sub_sessions (subscription_id, device_id, device_ua, sid, expires_at, last_seen) VALUES (?, ?, ?, ?, ?, datetime(\'now\'))',
  ).bind(sub.id, deviceId, ua.slice(0, 200), sid, sub.expires_at).run();

  const token = await signToken({ sub: `sub:${sub.id}`, sid, exp: sub.expires_at }, env.SUBSCRIBE_SECRET || 'dev-secret');
  return new Response(
    JSON.stringify({ ok: true, label: sub.label, expires_at: sub.expires_at, device_count: deviceCount + 1 }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Set-Cookie': setCookie(SUB_COOKIE, token, { maxAge, path: '/' }) },
    },
  );
}
