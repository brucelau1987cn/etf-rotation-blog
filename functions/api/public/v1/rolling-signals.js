import { asLkg, projectUpstream, validatePublicPayload } from '../../../_lib/a-rolling.js';

const MAX_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 8000;
const DEFAULT_STALE_AFTER_SECONDS = 900;

// Known multi/empty-side nodes used by the rolling board. Prefer deterministic
// get() over KV list() so free-plan list quotas cannot blank the live timeline.
const KNOWN_BUY_CODES = [
  '1.75h', '105m',
  '2h', '2.5h', '3h', '3.5h', '4h', '4.5h', '5h', '5.5h',
  '6h', '6.5h', '7h', '7.5h', '8h',
];
const KNOWN_SELL_CODES = [
  '10m',
  '15m', '30m', '60m', '90m', '120m', '150m', '180m', '210m', '240m',
];

const INSTRUMENT_SNAPSHOTS = {
  '600021': '/data/a-rolling-signals.json',
  '002173': '/data/a-rolling-signals-002173.json',
  '600703': '/data/a-rolling-signals-600703.json',
  '000021': '/data/a-rolling-signals-000021.json',
  '301511': '/data/a-rolling-signals-301511.json',
  '301362': '/data/a-rolling-signals-301362.json',
  '688041': '/data/a-rolling-signals-688041.json',
  '600637': '/data/a-rolling-signals-600637.json',
  '688825': '/data/a-rolling-signals-688825.json',
  '300077': '/data/a-rolling-signals-300077.json',
  '688008': '/data/a-rolling-signals-688008.json',
  '002185': '/data/a-rolling-signals-002185.json',
  '01378': '/data/a-rolling-signals-01378.json',
  'TSLA': '/data/a-rolling-signals-TSLA.json',
};

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

const normalizeSymbol = value => String(value || '').trim().toUpperCase().replace(/\.(SH|SZ|SS|HK|US)$/i, '');

const snapshotPathForSymbol = symbol => {
  const key = normalizeSymbol(symbol) || '600021';
  return INSTRUMENT_SNAPSHOTS[key] || null;
};

const emptyLkg = symbol => ({
  schema_version: 'a-rolling-energy-v4',
  mode: 'lkg',
  generated_at: new Date(0).toISOString(),
  data_as_of: null,
  freshness: 'stale',
  stale_after_seconds: DEFAULT_STALE_AFTER_SECONDS,
  delivery: { state: 'lkg', reason: '该标的暂无静态快照' },
  instrument: { instrument_name: symbol, exchange: 'UNKNOWN', symbol },
  transmission: { state: 'observing', basis: 'chronological_sequence', lit_count: 0, buy_count: 0, sell_count: 0 },
  timeline: [],
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
    return await fetch(new Request(url, {
      headers: { accept: 'application/json', 'user-agent': 'ETF-Rolling-Public/1.0' }
    }), { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
};

const loadLkg = async (request, env, symbol) => {
  const path = snapshotPathForSymbol(symbol);
  if (!path) return validatePublicPayload(emptyLkg(symbol));
  const url = new URL(path, request.url);
  const response = env.ASSETS?.fetch
    ? await env.ASSETS.fetch(new Request(url, { headers: { accept: 'application/json' } }))
    : await fetchWithTimeout(url, DEFAULT_TIMEOUT_MS);
  return validatePublicPayload(await readJsonResponse(response));
};

const parseSignalRecord = (value, symbol) => {
  if (!value) return null;
  try {
    const item = typeof value === 'string' ? JSON.parse(value) : value;
    if (normalizeSymbol(item.symbol) !== symbol || !['BUY', 'SELL'].includes(item.signal) || !item.cycle_code) {
      return null;
    }
    return {
      type: item.signal,
      code: String(item.cycle_code),
      label: String(item.cycle_code),
      triggered_at: item.trigger_time_utc || item.received_at,
      received_at: item.received_at || item.trigger_time_utc,
      event_id: item.event_id || null,
    };
  } catch {
    return null;
  }
};

const storageKey = (symbol, cycleCode, signal) => `signal:${symbol}:${cycleCode}:${signal}`;
const indexKey = symbol => `index:${symbol}`;

const candidateKeysFromIndex = (symbol, rawIndex) => {
  if (!rawIndex) return [];
  try {
    const parsed = typeof rawIndex === 'string' ? JSON.parse(rawIndex) : rawIndex;
    const entries = Array.isArray(parsed)
      ? parsed
      : Array.isArray(parsed?.keys)
        ? parsed.keys
        : Array.isArray(parsed?.entries)
          ? parsed.entries
          : [];
    return entries.flatMap(entry => {
      if (typeof entry === 'string' && entry.startsWith(`signal:${symbol}:`)) return [entry];
      if (!entry || typeof entry !== 'object') return [];
      if (typeof entry.key === 'string' && entry.key.startsWith(`signal:${symbol}:`)) return [entry.key];
      const cycle = entry.cycle_code || entry.code;
      const signal = entry.signal || entry.type;
      if (!cycle || !['BUY', 'SELL'].includes(signal)) return [];
      return [storageKey(symbol, cycle, signal)];
    });
  } catch {
    return [];
  }
};

const knownCandidateKeys = symbol => [
  ...KNOWN_BUY_CODES.map(code => storageKey(symbol, code, 'BUY')),
  ...KNOWN_SELL_CODES.map(code => storageKey(symbol, code, 'SELL')),
];

/**
 * Rebuild timeline with get() only.
 * Priority:
 *  1) index:{symbol} keys (maintained by webhook)
 *  2) known board cycle nodes
 * Never call kv.list — free plan list quota is only 1,000/day.
 */
const loadKvTimeline = async (kv, symbol) => {
  if (!kv?.get) return [];

  const indexRaw = await kv.get(indexKey(symbol));
  const candidates = [...new Set([
    ...candidateKeysFromIndex(symbol, indexRaw),
    ...knownCandidateKeys(symbol),
  ])];

  const raw = await Promise.all(candidates.map(name => kv.get(name)));
  return raw
    .map(value => parseSignalRecord(value, symbol))
    .filter(Boolean)
    .sort((a, b) => new Date(a.received_at || a.triggered_at).getTime() - new Date(b.received_at || b.triggered_at).getTime());
};

const publicReason = error => {
  if (error?.name === 'AbortError') return '上游请求超时';
  return '上游暂不可用或数据未通过校验';
};

export async function handleRollingSignals(request, env = {}) {
  const requestUrl = new URL(request.url);
  const symbol = normalizeSymbol(requestUrl.searchParams.get('symbol') || requestUrl.searchParams.get('code') || '600021');

  let lkg;
  try {
    lkg = await loadLkg(request, env, symbol);
  } catch {
    return json({ error: 'rolling signal snapshot unavailable', symbol }, 503);
  }

  if (env.ROLLING_KV) {
    try {
      const timeline = await loadKvTimeline(env.ROLLING_KV, symbol);
      if (timeline.length) {
        const latestReceivedAt = timeline
          .map(item => item.received_at || item.triggered_at)
          .filter(Boolean)
          .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
        return json(projectUpstream({
          instrument: lkg.instrument,
          timeline,
          data_as_of: latestReceivedAt,
        }));
      }
    } catch {
      return json(asLkg(lkg, 'KV信号读取失败，返回静态快照'));
    }
  }

  if (symbol !== '600021') {
    return json(asLkg(lkg, env.ROLLING_KV ? 'KV暂无该标的信号，返回静态快照' : 'ROLLING_KV未绑定，返回静态快照'));
  }

  const upstreamUrl = String(env.A_ROLLING_UPSTREAM_URL || '').trim();
  if (!upstreamUrl) return json(asLkg(lkg, env.ROLLING_KV ? 'KV暂无信号且未配置只读上游' : 'ROLLING_KV未绑定且未配置只读上游'));

  try {
    const parsed = new URL(upstreamUrl);
    if (parsed.protocol !== 'https:') throw new Error('upstream URL must use HTTPS');
    const timeoutMs = Math.min(Math.max(Number(env.A_ROLLING_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS, 1000), 15000);
    const staleAfterSeconds = Math.min(
      Math.max(Number(env.A_ROLLING_STALE_AFTER_SECONDS) || DEFAULT_STALE_AFTER_SECONDS, 60),
      86400,
    );
    const upstreamRes = await fetchWithTimeout(parsed, timeoutMs);
    if (!upstreamRes.ok) throw new Error(`upstream returned HTTP ${upstreamRes.status}`);
    const upstream = await readJsonResponse(upstreamRes);
    return json(projectUpstream(upstream, new Date().toISOString(), staleAfterSeconds));
  } catch (error) {
    return json(asLkg(lkg, publicReason(error)));
  }
}

export async function onRequestGet({ request, env }) {
  return handleRollingSignals(request, env);
}
