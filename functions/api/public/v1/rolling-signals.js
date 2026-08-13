import { asLkg, projectUpstream, validatePublicPayload } from '../../../_lib/a-rolling.js';
import {
  loadRollingTimelinesFromD1,
  normalizeSymbol as normalizeRollingSymbol,
  shanghaiTradeDate,
  updateRollingSignalPriceIfMissing,
} from '../../../_lib/rolling-signals-d1.js';
import { findRollingInstrumentBySymbol, seedRollingInstrumentsIfEmpty } from '../../../_lib/rolling-instruments.js';
import { fetchKline1m, pickMinuteBar } from './kline.js';

const MAX_BYTES = 512 * 1024;
const DEFAULT_TIMEOUT_MS = 8000;
const DEFAULT_STALE_AFTER_SECONDS = 900;
const MAX_BATCH_SYMBOLS = 40;

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
  '02701': '/data/a-rolling-signals-02701.json',
  '01378': '/data/a-rolling-signals-01378.json',
  'TSLA': '/data/a-rolling-signals-TSLA.json',
  'SI=F': '/data/futures-rolling-signals-hf_XAG.json',
  'HF_XAG': '/data/futures-rolling-signals-hf_XAG.json',
};

const INSTRUMENT_META = {
  'SI=F': { instrument_name: '白银现货', exchange: 'FUTURES', symbol: 'SI=F' },
  'HF_XAG': { instrument_name: '白银现货', exchange: 'FUTURES', symbol: 'SI=F' },
};

const headers = state => ({
  'content-type': 'application/json; charset=utf-8',
  // Signals are day-locked; allow short shared cache so the board does not thrash origin.
  'cache-control': 'public, max-age=15, s-maxage=60, stale-while-revalidate=300',
  'x-content-type-options': 'nosniff',
  'x-rolling-delivery': state,
});

const json = (payload, status = 200, cacheControl = null) => new Response(JSON.stringify(payload), {
  status,
  headers: {
    ...headers(payload?.delivery?.state || 'error'),
    ...(cacheControl ? { 'cache-control': cacheControl } : {}),
  },
});

const normalizeSymbol = value => normalizeRollingSymbol(value);

const snapshotPathForSymbol = symbol => {
  const key = normalizeSymbol(symbol) || '600021';
  if (INSTRUMENT_SNAPSHOTS[key]) return INSTRUMENT_SNAPSHOTS[key];
  // Convention for admin-added instruments without hard-coded map entry.
  if (key === 'SI=F' || key === 'HF_XAG') return '/data/futures-rolling-signals-hf_XAG.json';
  if (/^[A-Z][A-Z0-9.\-]{0,9}$/.test(key) && !/^\d+$/.test(key) && key.includes('=')) {
    return `/data/futures-rolling-signals-${key.replace(/=/g, '_')}.json`;
  }
  return `/data/a-rolling-signals-${key}.json`;
};

const instrumentMetaForSymbol = symbol => INSTRUMENT_META[normalizeSymbol(symbol)] || null;

const applyDbInstrumentMeta = async (payload, env, symbol, seed = true) => {
  if (!payload || !env?.DB) return payload;
  try {
    const row = await findRollingInstrumentBySymbol(env.DB, symbol, { seed });
    if (!row) return payload;
    const next = {
      ...payload,
      instrument: {
        ...(payload.instrument || {}),
        instrument_name: row.name || payload.instrument?.instrument_name,
        exchange: row.exchange || payload.instrument?.exchange,
        symbol: row.symbol || payload.instrument?.symbol || symbol,
      },
    };
    if (row.start_date) {
      next.transmission = {
        ...(next.transmission || {}),
        start_date: row.start_date,
      };
    }
    return next;
  } catch {
    return payload;
  }
};


const applyInstrumentMeta = (payload, symbol) => {
  const meta = instrumentMetaForSymbol(symbol);
  if (!meta || !payload) return payload;
  return { ...payload, instrument: { ...(payload.instrument || {}), ...meta } };
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
  return applyInstrumentMeta(validatePublicPayload(await readJsonResponse(response)), symbol);
};

const mergeTimelines = (today = null, ...lists) => {
  const map = new Map();
  const isToday = item => {
    if (!today) return false;
    const ts = item.received_at || item.triggered_at;
    return Boolean(ts) && shanghaiTradeDate(ts) === today;
  };
  for (const list of lists) {
    for (const item of list || []) {
      if (!item || !item.type || !item.code) continue;
      const key = `${item.type}:${item.code}`;
      const prev = map.get(key);
      if (!prev) {
        map.set(key, item);
        continue;
      }
      // Today's D1 row wins over any older projection (static LKG or historical D1).
      // Cross-day rows must not shadow the live day board.
      const prevIsToday = isToday(prev);
      const itemIsToday = isToday(item);
      if (itemIsToday && !prevIsToday) {
        map.set(key, {
          ...prev,
          ...item,
          price: item.price ?? prev.price ?? null,
          price_source: item.price_source ?? prev.price_source ?? null,
        });
        continue;
      }
      if (prevIsToday) continue;
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

const requestedSymbols = requestUrl => {
  const raw = String(requestUrl.searchParams.get('symbols') || '').trim();
  if (!raw) {
    const single = normalizeSymbol(requestUrl.searchParams.get('symbol') || requestUrl.searchParams.get('code') || '');
    return single ? [single] : [];
  }
  const seen = new Set();
  const out = [];
  for (const part of raw.split(',')) {
    const symbol = normalizeSymbol(part);
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    out.push(symbol);
    if (out.length >= MAX_BATCH_SYMBOLS) break;
  }
  return out;
};

const queueMissingPriceBackfill = (waitUntil, env, symbol, d1Timeline) => {
  if (typeof waitUntil !== 'function' || !env?.DB) return;
  const missingPriceItems = (d1Timeline || []).filter(item => item.price == null && item.event_id);
  if (!missingPriceItems.length) return;
  waitUntil((async () => {
    for (const item of missingPriceItems) {
      try {
        const atTime = item.triggered_at || item.received_at;
        const klineRes = await fetchKline1m(symbol, { at: atTime });
        const bar = klineRes?.bar || pickMinuteBar(klineRes?.bars, atTime);
        if (bar?.close != null) {
          await updateRollingSignalPriceIfMissing(env.DB, {
            trade_date: shanghaiTradeDate(atTime),
            symbol,
            cycle_code: item.code,
            signal: item.type,
            event_id: item.event_id,
            trigger_price: bar.close,
            trigger_price_source: bar.source || 'kline-1m',
          });
        }
      } catch (e) {
        console.warn('background price backfill failed for', symbol, item.event_id, e);
      }
    }
  })());
};

const assembleBoard = async ({ request, env, waitUntil, symbol, lkg, d1Timeline, tradeDate, seedInstrumentMeta = true }) => {
  if (env.DB) {
    try {
      queueMissingPriceBackfill(waitUntil, env, symbol, d1Timeline);
      const staticTimeline = Array.isArray(lkg.timeline) ? lkg.timeline : [];
      const timeline = mergeTimelines(tradeDate, staticTimeline, d1Timeline);
      if (timeline.length) {
        const latestReceivedAt = timeline
          .map(item => item.received_at || item.triggered_at)
          .filter(Boolean)
          .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
        const payload = applyInstrumentMeta(projectUpstream({
          instrument: lkg.instrument,
          timeline,
          data_as_of: latestReceivedAt,
        }), symbol);
        const todayD1 = (d1Timeline || []).filter(item => shanghaiTradeDate(item.triggered_at || item.received_at) === tradeDate);
        payload.mode = todayD1.length ? 'live' : payload.mode;
        payload.freshness = todayD1.length ? 'fresh' : payload.freshness;
        payload.delivery = {
          state: todayD1.length ? 'live' : (payload.delivery?.state || 'lkg'),
          reason: todayD1.length ? null : (payload.delivery?.reason || 'D1当日暂无新信号，展示静态投影'),
        };
        payload.trade_date = tradeDate;
        payload.storage = 'd1';
        const withDb = await applyDbInstrumentMeta(payload, env, symbol, seedInstrumentMeta);
        if (!withDb.transmission?.start_date && lkg.transmission?.start_date) {
          withDb.transmission = {
            ...withDb.transmission,
            start_date: lkg.transmission.start_date,
          };
        }
        return { status: 200, payload: withDb };
      }
    } catch {
      return { status: 200, payload: asLkg(lkg, 'D1信号读取失败，返回静态快照') };
    }
  }

  if (symbol !== '600021') {
    return { status: 200, payload: asLkg(lkg, env.DB ? 'D1暂无该标的信号，返回静态快照' : 'DB未绑定，返回静态快照') };
  }

  const upstreamUrl = String(env.A_ROLLING_UPSTREAM_URL || '').trim();
  if (!upstreamUrl) return { status: 200, payload: asLkg(lkg, env.DB ? 'D1暂无信号且未配置只读上游' : 'DB未绑定且未配置只读上游') };

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
    return { status: 200, payload: projectUpstream(upstream, new Date().toISOString(), staleAfterSeconds) };
  } catch (error) {
    return { status: 200, payload: asLkg(lkg, publicReason(error)) };
  }
};

const loadBoardForSymbol = async ({ request, env, waitUntil, symbol, tradeDate, d1Timeline, seedInstrumentMeta = true }) => {
  let lkg;
  try {
    lkg = await loadLkg(request, env, symbol);
    lkg = await applyDbInstrumentMeta(lkg, env, symbol, seedInstrumentMeta);
  } catch {
    return { status: 503, payload: { error: 'rolling signal snapshot unavailable', symbol } };
  }
  return assembleBoard({ request, env, waitUntil, symbol, lkg, d1Timeline: d1Timeline || [], tradeDate, seedInstrumentMeta });
};

export async function handleRollingSignals(request, env = {}, waitUntil = null) {
  const requestUrl = new URL(request.url);
  const symbols = requestedSymbols(requestUrl);
  const tradeDate = shanghaiTradeDate();
  const batch = symbols.length > 1 || Boolean(String(requestUrl.searchParams.get('symbols') || '').trim());
  const targets = symbols.length ? symbols : ['600021'];

  let timelines = new Map(targets.map(symbol => [symbol, []]));
  if (env.DB) {
    try {
      timelines = await loadRollingTimelinesFromD1(env.DB, targets, null);
    } catch {
      timelines = new Map(targets.map(symbol => [symbol, []]));
    }
  }

  if (!batch) {
    const result = await loadBoardForSymbol({
      request,
      env,
      waitUntil,
      symbol: targets[0],
      tradeDate,
      d1Timeline: timelines.get(targets[0]) || [],
    });
    return json(result.payload, result.status);
  }

  if (env.DB) {
    try {
      await seedRollingInstrumentsIfEmpty(env.DB);
    } catch {
      // Instrument metadata is optional; boards still serve LKG/D1 signal data.
    }
  }

  const results = await Promise.all(targets.map(symbol => loadBoardForSymbol({
    request,
    env,
    waitUntil,
    symbol,
    tradeDate,
    d1Timeline: timelines.get(symbol) || [],
    seedInstrumentMeta: false,
  })));
  const boards = [];
  const errors = [];
  results.forEach((result, index) => {
    if (result.status === 200) boards.push(result.payload);
    else errors.push({ symbol: targets[index], error: result.payload?.error || 'rolling signal snapshot unavailable' });
  });
  const status = boards.length ? 200 : 503;

  return json({
    ok: errors.length === 0,
    schema_version: 'a-rolling-energy-batch-v1',
    trade_date: tradeDate,
    count: boards.length,
    boards,
    errors,
  }, status, errors.length ? 'no-store' : null);
}

export async function onRequestGet({ request, env, waitUntil }) {
  return handleRollingSignals(request, env, waitUntil);
}
