import { clearSessionCookie, getCookie, json, sha256 } from '../../_lib/auth.js';

export async function onRequestPost({ request, env }) {
  const token = getCookie(request, 'session');
  if (token) await env.DB.prepare('DELETE FROM sessions WHERE token_hash = ?').bind(await sha256(token)).run();
  return new Response(JSON.stringify({ ok: true }), { headers: { 'content-type': 'application/json', 'set-cookie': clearSessionCookie(), 'cache-control': 'no-store' } });
}
