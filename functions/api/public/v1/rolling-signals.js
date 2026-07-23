import { asLkg, projectUpstream, validatePublicPayload } from '../../../_lib/a-rolling.js';

const MAX_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 8000;
const DEFAULT_STALE_AFTER_SECONDS = 900;

const headers = state => ({
  'content-type': 'application/json; charset=utf-8',
  'cache-control': 'public, max-age=0, s-maxage=30, stale-while-revalidate=120',
  'x-content-type-options': 'nosniff',
  'x-rolling-delivery': state,
});

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: headers(payload?.delivery?.state || 'error'),
});

const readJsonResponse = async response => {
  if (!response.ok) throw new Error(`source returned HTTP ${response.status}`);
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.toLowerCase().includes('application/json')) throw new Error('source returned a non-JSON response');
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > MAX_BYTES) throw new Error('source payload exceeds size limit');
  return JSON.parse(text);
};

const fetchWithTimeout = async (url, timeoutMs) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      headers: { accept: 'application/json', 'user-agent': 'ETF-Rolling-Public/1.0' },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
};

const loadLkg = async (request, env) => {
  const url = new URL('/data/a-rolling-signals.json', request.url);
  const response = env.ASSETS?.fetch
    ? await env.ASSETS.fetch(new Request(url, { headers: { accept: 'application/json' } }))
    : await fetchWithTimeout(url, DEFAULT_TIMEOUT_MS);
  return validatePublicPayload(await readJsonResponse(response));
};

const publicReason = error => {
  if (error?.name === 'AbortError') return '上游请求超时';
  return '上游暂不可用或数据未通过校验';
};

export async function handleRollingSignals(request, env = {}) {
  let lkg;
  try {
    lkg = await loadLkg(request, env);
  } catch {
    return json({ error: 'rolling signal snapshot unavailable' }, 503);
  }

  const upstreamUrl = String(env.A_ROLLING_UPSTREAM_URL || '').trim();
  if (!upstreamUrl) return json(asLkg(lkg, '尚未配置只读上游信号源'));

  try {
    const parsed = new URL(upstreamUrl);
    if (parsed.protocol !== 'https:') throw new Error('upstream URL must use HTTPS');
    const timeoutMs = Math.min(Math.max(Number(env.A_ROLLING_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS, 1000), 15000);
    const staleAfterSeconds = Math.min(
      Math.max(Number(env.A_ROLLING_STALE_AFTER_SECONDS) || DEFAULT_STALE_AFTER_SECONDS, 60),
      86400,
    );
    const upstream = await readJsonResponse(await fetchWithTimeout(parsed, timeoutMs));
    return json(projectUpstream(upstream, new Date().toISOString(), staleAfterSeconds));
  } catch (error) {
    return json(asLkg(lkg, publicReason(error)));
  }
}

export async function onRequestGet({ request, env }) {
  return handleRollingSignals(request, env);
}
