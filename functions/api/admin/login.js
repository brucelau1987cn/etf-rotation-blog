// POST /api/admin/login
// Body: { username, password, remember?, captcha_token? }
// 校验管理员用户名+密码（D1 admin_credentials 表，可运行时修改）
// remember=true → cookie 30 天（记住本机设备）；否则 12 小时
// captcha_token 预留：env.CAPTCHA_ENABLED='true' 时启用（当前未接入验证码服务）
import { sha256Hex, signToken, setCookie, ADMIN_COOKIE } from '../../_lib/subscription-auth.js';

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
  const remember = body.remember === true;
  const captchaToken = String(body.captcha_token || '').trim();

  // 验证码预留接口：启用时校验（当前返回未启用）
  if ((env.CAPTCHA_ENABLED || '') === 'true') {
    if (!captchaToken) {
      return Response.json({ ok: false, error: '请完成验证码校验', need_captcha: true }, { status: 403 });
    }
    // TODO: 接入验证码服务（预留 — 当前无验证码后端，直接拒绝防止绕过）
    return Response.json({ ok: false, error: '验证码服务未配置，请联系管理员' }, { status: 501 });
  }

  if (!username || !password) {
    return Response.json({ ok: false, error: '请输入用户名和密码' }, { status: 400 });
  }

  // 从 D1 读取管理员凭据（按用户名查询，支持多管理员）
  const row = await env.DB.prepare(
    'SELECT username, password_hash, role FROM admin_credentials WHERE username = ? LIMIT 1',
  ).bind(username).first();

  const storedUser = row?.username || '';
  const storedHash = row?.password_hash || '';
  const hash = await sha256Hex(`${username}:${password}`);
  if (!storedHash || username !== storedUser || hash !== storedHash) {
    return Response.json({ ok: false, error: '用户名或密码错误' }, { status: 401 });
  }

  const role = row.role === 'admin' ? 'admin' : 'super_admin';
  const ttlSec = remember ? 30 * 24 * 3600 : 12 * 3600; // 记住本机=30天，否则12小时
  const exp = new Date(Date.now() + ttlSec * 1000).toISOString();
  const token = await signToken({ role, exp }, env.ADMIN_SECRET || 'dev-admin-secret');
  return new Response(
    JSON.stringify({ ok: true, expires_at: exp, remember, role }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Set-Cookie': setCookie(ADMIN_COOKIE, token, { maxAge: ttlSec, path: '/' }),
      },
    },
  );
}
