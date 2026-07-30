import { asLkg, projectUpstream, validatePublicPayload } from '../../../_lib/a-rolling.js';
import {
  loadRollingTimelineFromD1,
  normalizeSymbol as normalizeRollingSymbol,
  shanghaiTradeDate,
} from '../../../_lib/rolling-signals-d1.js';

const MAX_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 8000;
const DEFAULT_STALE_AFTER_SECONDS = 900;

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
  '002185': '/data/a-rolling-signals-002185.json',
  '06809': '/data/a-rolling-signals-06809.json',
  '01378': '/data/a-rolling-signals-01378.json',
  'TSLA': '/data/a-rolling-signals-TSLA.json',
};

const headers = state => ({
  'content-type': 'application/json; charset=utf-8',
  // Signals are day-locked; allow short shared cache so the board does not thrash origin.
  'cache-control': 'public, max-age=15, s-maxage=60, stale-while-revalidate=300',
  'x-content-type-options': 'nosniff',
  'x-rolling-delivery': state,
});

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: headers(payload?.delivery?.state || 'error'),
});

const normalizeSymbol = value => normalizeRollingSymbol(value);

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

const mergeTimelines = (...lists) => {
  const map = new Map();
  for (const list of lists) {
    for (const item of list || []) {
      if (!item || !item.type || !item.code) continue;
      const key = `${item.type}:${item.code}`;
      const prev = map.get(key);
      if (!prev) {
        map.set(key, item);
        continue;
      }
      // Keep earliest formal receipt for the day/node, but preserve D1 price if available.
      const prevTs = new Date(prev.received_at || prev.triggered_at || 0).getTime();
      const nextTs = new Date(item.received_at || item.triggered_at || 0).getTime();
      if (Math.abs(prevTs - nextTs) < 5000 || prev.event_id === item.event_id) {
        map.set(key, {
          ...prev,
          ...item,
          price: item.price ?? prev.price ?? null,
          price_source: item.price_source ?? prev.price_source ?? null,
        });
      } else if (Number.isFinite(nextTs) && (!Number.isFinite(prevTs) || nextTs < prevTs)) {
        map.set(key, {
          ...prev,
          ...item,
          price: item.price ?? prev.price ?? null,
          price_source: item.price_source ?? prev.price_source ?? null,
        });
      } else {
        map.set(key, {
          ...item,
          ...prev,
          price: prev.price ?? item.price ?? null,
          price_source: prev.price_source ?? item.price_source ?? null,
        });
      }
    }
  }
  return [...map.values()].sort(
    (a, b) => new Date(a.received_at || a.triggered_at).getTime() - new Date(b.received_at || b.triggered_at).getTime(),
  );
};

const publicReason = error => {
  if (error?.name === 'AbortError') return '上游请求超时';
  return '上游暂不可用或数据未通过校验';
};

export async function handleRollingSignals(request, env = {}) {
  const requestUrl = new URL(request.url);
  const symbol = normalizeSymbol(requestUrl.searchParams.get('symbol') || requestUrl.searchParams.get('code') || '600021');
  const tradeDate = shanghaiTradeDate();

  let lkg;
  try {
    lkg = await loadLkg(request, env, symbol);
  } catch {
    return json({ error: 'rolling signal snapshot unavailable', symbol }, 503);
  }

  // Primary path: D1 day board. No KV quota involved.
  if (env.DB) {
    try {
      // Query all stored D1 signals for this symbol (not only today) so historical signal prices are preserved.
      const d1Timeline = await loadRollingTimelineFromD1(env.DB, symbol, null);
      // Merge with static LKG so authorized historical projections remain visible.
      const staticTimeline = Array.isArray(lkg.timeline) ? lkg.timeline : [];
      const timeline = mergeTimelines(staticTimeline, d1Timeline);
      if (timeline.length) {
        const latestReceivedAt = timeline
          .map(item => item.received_at || item.triggered_at)
          .filter(Boolean)
          .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
        const payload = projectUpstream({
          instrument: lkg.instrument,
          timeline,
          data_as_of: latestReceivedAt,
        });
        const todayD1 = d1Timeline.filter(item => shanghaiTradeDate(item.triggered_at || item.received_at) === tradeDate);
        // Day-locked board: once written, keep presentation stable for the day.
        payload.mode = todayD1.length ? 'live' : payload.mode;
        payload.freshness = todayD1.length ? 'fresh' : payload.freshness;
        payload.delivery = {
          state: todayD1.length ? 'live' : (payload.delivery?.state || 'lkg'),
          reason: todayD1.length ? null : (payload.delivery?.reason || 'D1当日暂无新信号，展示静态投影'),
        };
        payload.trade_date = tradeDate;
        payload.storage = 'd1';
        if (lkg.transmission?.start_date) {
          payload.transmission = {
            ...payload.transmission,
            start_date: lkg.transmission.start_date,
          };
        }
        return json(payload);
      }
    } catch {
      return json(asLkg(lkg, 'D1信号读取失败，返回静态快照'));
    }
  }

  if (symbol !== '600021') {
    return json(asLkg(lkg, env.DB ? 'D1暂无该标的信号，返回静态快照' : 'DB未绑定，返回静态快照'));
  }

  const upstreamUrl = String(env.A_ROLLING_UPSTREAM_URL || '').trim();
  if (!upstreamUrl) return json(asLkg(lkg, env.DB ? 'D1暂无信号且未配置只读上游' : 'DB未绑定且未配置只读上游'));

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
