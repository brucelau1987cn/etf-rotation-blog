// POST /api/public/v1/subscription/login
// Body: { passphrase }
// 校验订阅密码（D1 subscriptions 表，sha256 匹配 + 未撤销 + 未过期）→ 签发订阅 cookie
import { sha256Hex, signToken, setCookie, SUB_COOKIE } from '../../../../_lib/subscription-auth.js';

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
  const result = await env.DB.prepare(
    `SELECT id, label, expires_at, revoked FROM subscriptions WHERE passphrase_hash = ? LIMIT 1`,
  ).bind(hash).first();

  if (!result || result.revoked) {
    return Response.json({ ok: false, error: '密码无效' }, { status: 401 });
  }
  const expiresMs = Date.parse(result.expires_at);
  if (Number.isNaN(expiresMs) || expiresMs <= Date.now()) {
    return Response.json({ ok: false, error: `订阅已过期（${result.expires_at}）` }, { status: 403 });
  }

  // 签发 token：载荷含过期时间，过期即失效
  const token = await signToken(
    { sub: `sub:${result.id}`, exp: result.expires_at },
    env.SUBSCRIBE_SECRET || 'dev-secret',
  );
  const maxAge = Math.max(60, Math.floor((expiresMs - Date.now()) / 1000));
  return new Response(
    JSON.stringify({ ok: true, label: result.label, expires_at: result.expires_at }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Set-Cookie': setCookie(SUB_COOKIE, token, { maxAge, path: '/' }),
      },
    },
  );
}
