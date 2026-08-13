import { readCookie, setCookie, sha256Hex, signToken, verifyToken } from '../../../_lib/subscription-auth.js';

const WINDOW_SECONDS = 120;
const CLEANUP_SECONDS = 600;
const MAX_NEW_IDENTITIES_PER_IP = 20;
const COOKIE_NAME = 'etf_presence';
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

const json = (body, status = 200, cookie = '') => {
  const headers = new Headers({
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
  });
  if (cookie) headers.append('set-cookie', cookie);
  return new Response(JSON.stringify(body), { status, headers });
};

const randomId = () => {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
};

async function existingIdentity(request, secret) {
  const token = readCookie(request.headers.get('Cookie'), COOKIE_NAME);
  const payload = await verifyToken(token, secret);
  if (!payload?.visitor_id || typeof payload.visitor_id !== 'string') return null;
  if (!payload.exp || Number(payload.exp) <= Math.floor(Date.now() / 1000)) return null;
  return payload.visitor_id;
}

export async function onRequestPost({ request, env }) {
  if (!env.DB || !env.PRESENCE_SECRET) return json({ error: 'presence unavailable' }, 503);

  const now = Math.floor(Date.now() / 1000);
  const ip = String(request.headers.get('CF-Connecting-IP') || 'unknown').slice(0, 80);
  const ipKey = await sha256Hex(`${env.PRESENCE_SECRET}:ip:${ip}`);
  let visitorId = await existingIdentity(request, env.PRESENCE_SECRET);
  let cookie = '';

  if (!visitorId) {
    visitorId = randomId();
    const inserted = await env.DB.prepare(`INSERT INTO presence_sessions (visitor_id, last_seen, ip_key)
      SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM presence_sessions WHERE ip_key = ? AND last_seen >= ?) < ?`)
      .bind(visitorId, now, ipKey, ipKey, now - CLEANUP_SECONDS, MAX_NEW_IDENTITIES_PER_IP)
      .run();
    if (Number(inserted?.meta?.changes || 0) !== 1) {
      return json({ error: 'presence identity limit' }, 429);
    }
    const token = await signToken({ visitor_id: visitorId, exp: now + COOKIE_MAX_AGE }, env.PRESENCE_SECRET);
    cookie = setCookie(COOKIE_NAME, token, { maxAge: COOKIE_MAX_AGE, path: '/', secure: true });
  } else {
    await env.DB.prepare(`INSERT INTO presence_sessions (visitor_id, last_seen, ip_key) VALUES (?, ?, ?)
      ON CONFLICT(visitor_id) DO UPDATE SET last_seen = excluded.last_seen, ip_key = excluded.ip_key`)
      .bind(visitorId, now, ipKey)
      .run();
  }

  // Run bounded cleanup occasionally; every 16th request avoids a DELETE on every heartbeat.
  if ((now & 15) === 0) {
    await env.DB.prepare('DELETE FROM presence_sessions WHERE last_seen < ?').bind(now - CLEANUP_SECONDS).run();
  }

  const row = await env.DB.prepare('SELECT COUNT(*) AS online FROM presence_sessions WHERE last_seen >= ?')
    .bind(now - WINDOW_SECONDS)
    .first();

  return json({ online: Number(row?.online || 0), window_seconds: WINDOW_SECONDS }, 200, cookie);
}
