// GET /api/public/v1/price-volume-tag?s=33_002173,17_600021,SI=F
// 为股票/期货卡片计算「量价 12 态」标签（A股：THS 日K；期货：Yahoo 日K）
// 价态：|Δclose|<=0.3% 平 / >0.3% 涨 / <-0.3% 跌
// 量态：vol/prevVol >=1.1 增 / <=0.9 缩 / 其余 平
// 特殊：量比>=3 天量异动；量比<=0.3 且价平/跌 地量企稳；量比>=1.8 且价涨且创20日新高 放量突破
const TTL = 60;
const cache = new Map(); // key -> { expiresAt, payload }

const TAGS = {
  1: { id: 1, name: '放量突破', cls: 'red' },
  2: { id: 2, name: '天量异动', cls: 'amber' },
  3: { id: 3, name: '地量企稳', cls: 'green' },
  4: { id: 4, name: '价涨量增', cls: 'red' },
  5: { id: 5, name: '价涨量平', cls: 'red' },
  6: { id: 6, name: '价涨量缩', cls: 'amber' },
  7: { id: 7, name: '价平量增', cls: 'amber' },
  8: { id: 8, name: '价平量平', cls: 'amber' },
  9: { id: 9, name: '价平量缩', cls: 'amber' },
  10: { id: 10, name: '价跌量增', cls: 'green' },
  11: { id: 11, name: '价跌量平', cls: 'green' },
  12: { id: 12, name: '价跌量缩', cls: 'green' },
};

function jsonOk(payload, ttl = TTL) {
  return Response.json(payload, { headers: { 'Cache-Control': `public, max-age=${ttl}` } });
}

function parseThsKline(raw) {
  // "date,open,high,low,close,vol(手),amount,turnover,,,0;..."
  return String(raw || '').split(';').filter(Boolean).map((seg) => {
    const f = seg.split(',');
    return {
      date: f[0], open: Number(f[1]), high: Number(f[2]), low: Number(f[3]),
      close: Number(f[4]), vol: Number(f[5]) * 100, amount: Number(f[6]),
    };
  });
}

function classify(recs) {
  if (!recs || recs.length < 2) return null;
  const prev = recs[recs.length - 2];
  const cur = recs[recs.length - 1];
  if (!Number.isFinite(cur.close) || !Number.isFinite(prev.close) || prev.close <= 0) return null;
  const pctChg = (cur.close - prev.close) / prev.close * 100;
  const volRatio = prev.vol > 0 ? cur.vol / prev.vol : null;
  // 20 日新高（突破判定用，含今日）
  const window = recs.slice(-20);
  const high20 = Math.max(...window.map((r) => r.high || 0));
  const priceState = pctChg > 0.3 ? 'up' : pctChg < -0.3 ? 'down' : 'flat';
  const volState = volRatio == null ? 'flat' : volRatio >= 1.1 ? 'up' : volRatio <= 0.9 ? 'down' : 'flat';

  let id;
  if (volRatio != null && volRatio >= 3) {
    id = 2; // 天量异动（巨量优先）
  } else if (volRatio != null && volRatio <= 0.3 && priceState !== 'up') {
    id = 3; // 地量企稳
  } else if (volRatio != null && volRatio >= 1.8 && priceState === 'up' && cur.high >= high20) {
    id = 1; // 放量突破
  } else {
    const map = {
      'up_up': 4, 'up_flat': 5, 'up_down': 6,
      'flat_up': 7, 'flat_flat': 8, 'flat_down': 9,
      'down_up': 10, 'down_flat': 11, 'down_down': 12,
    };
    id = map[`${priceState}_${volState}`] || 8;
  }
  return { ...TAGS[id], pct_chg: pctChg, vol_ratio: volRatio == null ? null : Number(volRatio.toFixed(2)), close: cur.close, prev_close: prev.close, date: cur.date };
}

async function fetchThsKline(code) {
  // THS kline 代理：取当年日线（A股 vol 手→股）
  const url = `https://d.10jqka.com.cn/v6/line/${code}/01/2026.js`;
  const resp = await fetch(url, {
    headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', Referer: 'https://stockpage.10jqka.com.cn/' },
    cf: { cacheTtl: 120, cacheEverything: true },
  });
  if (!resp.ok) throw new Error(`THS ${resp.status}`);
  const text = await resp.text();
  const m = text.match(/\(([\s\S]*)\)\s*;?\s*$/);
  if (!m) throw new Error('THS parse fail');
  const data = JSON.parse(m[1]);
  return parseThsKline(data.data);
}

async function fetchYahooKline(symbol) {
  // Yahoo chart：近 10 日日 K（期货 SI=F / GC=F / CL=F）
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=25d&interval=1d`;
  const resp = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, cf: { cacheTtl: 120, cacheEverything: true } });
  if (!resp.ok) throw new Error(`Yahoo ${resp.status}`);
  const body = await resp.json();
  const res = body?.chart?.result?.[0];
  if (!res) throw new Error('Yahoo empty');
  const ts = res.timestamp || [];
  const q = res.indicators?.quote?.[0] || {};
  const out = [];
  for (let i = 0; i < ts.length; i++) {
    out.push({
      date: new Date(ts[i] * 1000).toISOString().slice(0, 10),
      open: q.open?.[i], high: q.high?.[i], low: q.low?.[i],
      close: q.close?.[i], vol: q.volume?.[i] || 0,
    });
  }
  return out.filter((r) => Number.isFinite(r.close) && r.close > 0);
}

export async function onRequestGet(context) {
  const { request } = context;
  const url = new URL(request.url);
  const symbols = String(url.searchParams.get('s') || '').split(',').map((s) => s.trim()).filter(Boolean).slice(0, 30);
  if (symbols.length === 0) return jsonOk({ ok: false, error: 'missing s' });

  const cacheKey = symbols.join(',');
  const hit = cache.get(cacheKey);
  if (hit && hit.expiresAt > Date.now()) return jsonOk(hit.payload);

  const results = {};
  await Promise.all(symbols.map(async (raw) => {
    try {
      const isA = /^\d+_\d+$/.test(raw); // A股格式 33_002173 / 17_600021
      const recs = isA ? await fetchThsKline(raw) : await fetchYahooKline(raw);
      const tag = classify(recs);
      results[raw] = tag ? { ok: true, ...tag } : { ok: false, error: 'insufficient data' };
    } catch (e) {
      results[raw] = { ok: false, error: String(e.message || e) };
    }
  }));

  const payload = { ok: true, generated_at: new Date().toISOString(), tags: results };
  cache.set(cacheKey, { expiresAt: Date.now() + TTL * 1000, payload });
  return jsonOk(payload);
}
