// GET /api/public/v1/polymarket-prob?q=cpi-yoy
//
// 通过 gamma-api 发现「当前活跃」的月度事件，而不是硬编码某个月的 slug。
// Polymarket 每月新开一轮 CPI 市场、旧事件结算后 endDate 定格，硬编码 slug 会让
// 页面长期显示已结算的 100%/0% 概率（2026-10 实测：全部 4 个 slug 仍指向 7 月事件）。
const TTL = 300;
const DISCOVERY_TTL = 1800;
const cache = new Map();
let discovery = { at: 0, events: [] };

// q → 事件标题/ slug 的匹配规则（Polymarket 每月 slug 形如 october-inflation-us-annual）
const MATCHERS = {
  'cpi-yoy': {
    tag: 'cpi',
    title: /^[A-Z][a-z]+ Inflation US - Annual$/,
    slug: /^[a-z]+-inflation-us-annual$/,
  },
  'cpi-mom': {
    tag: 'cpi',
    title: /^[A-Z][a-z]+ Inflation US - Monthly$/,
    slug: /^[a-z]+-inflation-us-monthly$/,
  },
  'core-cpi-yoy': {
    tag: 'core-cpi',
    title: /^Core CPI YoY - [A-Z][a-z]+ \d{4}$/,
    slug: /^core-cpi-yoy-[a-z]+-\d{4}$/,
  },
  'core-cpi-mom': {
    tag: 'core-cpi',
    title: /^Core CPI MoM - [A-Z][a-z]+ \d{4}$/,
    slug: /^core-cpi-mom-[a-z]+-\d{4}$/,
  },
};

function parsePrices(market) {
  // outcomePrices 可能为数组 / "0.0185,0.9815" / "['0.0185','0.9815']"
  const raw = Array.isArray(market.outcomePrices)
    ? market.outcomePrices
    : (String(market.outcomePrices || '').match(/-?\d+(?:\.\d+)?/g) || []);
  return raw.map((p) => Number(String(p).trim()) * 100);
}

function isEnded(event, nowMs) {
  const end = Date.parse(String(event.endDate || ''));
  // 没有 endDate 的事件无法判定，按未结束处理（由上游 closed=false 过滤兜底）
  if (!Number.isFinite(end)) return false;
  return end <= nowMs;
}

async function fetchActiveEvents(tag) {
  const url = `https://gamma-api.polymarket.com/events?closed=false&limit=200&tag_slug=${encodeURIComponent(tag)}`;
  const resp = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!resp.ok) throw new Error(`Gamma ${resp.status}`);
  const body = await resp.json();
  return Array.isArray(body) ? body : [];
}

async function discover(nowMs) {
  if (discovery.events.length && nowMs - discovery.at < DISCOVERY_TTL * 1000) {
    return discovery.events;
  }
  const tags = [...new Set(Object.values(MATCHERS).map((m) => m.tag))];
  const events = [];
  for (const tag of tags) {
    for (const event of await fetchActiveEvents(tag)) {
      if (!isEnded(event, nowMs)) events.push(event);
    }
  }
  discovery = { at: nowMs, events };
  return events;
}

function pickEvent(events, matcher) {
  return events.find((event) => {
    const title = String(event.title || '');
    const slug = String(event.slug || '');
    return matcher.title.test(title) || matcher.slug.test(slug);
  });
}

const MONTHS = {
  january: '01', february: '02', march: '03', april: '04', may: '05', june: '06',
  july: '07', august: '08', september: '09', october: '10', november: '11', december: '12',
};

// 从事件标题解析它覆盖的数据月份（"October Inflation US - Annual" → 2026-10）。
// CPI 的 N 月数据在 N+1 月发布，前端据此拒绝把 10 月市场贴到 12 月的日历事件上。
function coveredMonth(event) {
  const title = String(event.title || '');
  const name = (title.match(/\b(January|February|March|April|May|June|July|August|September|October|November|December)\b/i) || [])[1];
  if (!name) return null;
  const mm = MONTHS[name.toLowerCase()];
  const explicitYear = (title.match(/\b(20\d{2})\b/) || [])[1];
  if (explicitYear) return `${explicitYear}-${mm}`;
  // 无年份时按事件结算日回推：结算在该月之后，故取 endDate 所在年（跨年时减 1）
  const end = Date.parse(String(event.endDate || ''));
  if (!Number.isFinite(end)) return null;
  const endYear = new Date(end).getUTCFullYear();
  const endMonth = new Date(end).getUTCMonth() + 1;
  const year = Number(mm) > endMonth ? endYear - 1 : endYear;
  return `${year}-${mm}`;
}

function shapePayload(q, event, nowMs) {
  const markets = (event.markets || []).map((m) => {
    const prices = parsePrices(m);
    return {
      id: m.id,
      question: m.question,
      yes_prob: Number((prices[0] ?? 0).toFixed(1)),
      no_prob: Number((prices[1] ?? 0).toFixed(1)),
      end_date: m.endDate || null,
    };
  }).sort((a, b) => b.yes_prob - a.yes_prob);
  return {
    ok: true,
    q,
    event: event.title,
    slug: event.slug,
    end_date: event.endDate || null,
    covered_month: coveredMonth(event),
    expired: false,
    updated_at: new Date(nowMs).toISOString(),
    top: markets[0] || null,
    markets: markets.slice(0, 8),
  };
}

// 测试专用：模块级缓存（cache / discovery）会跨用例泄漏，测试前调用可重置。
export function __resetCaches() {
  cache.clear();
  discovery = { at: 0, events: [] };
}

export async function onRequestGet(context) {
  const { request } = context;
  const url = new URL(request.url);
  const q = String(url.searchParams.get('q') || '').toLowerCase().trim();
  if (!q || !MATCHERS[q]) {
    return Response.json(
      { ok: false, error: 'unknown q; available: ' + Object.keys(MATCHERS).join(',') },
      { status: 400 },
    );
  }

  const nowMs = Date.now();
  const hit = cache.get(q);
  if (hit && hit.expiresAt > nowMs) {
    return Response.json(hit.payload, { headers: { 'Cache-Control': `public, max-age=${TTL}` } });
  }

  try {
    const event = pickEvent(await discover(nowMs), MATCHERS[q]);
    if (!event) {
      return Response.json(
        { ok: false, error: 'no active event for this query', q },
        { status: 404, headers: { 'Cache-Control': 'no-store' } },
      );
    }
    if (isEnded(event, nowMs)) {
      return Response.json(
        { ok: false, error: 'event expired; no active market', q, event: event.title },
        { status: 404, headers: { 'Cache-Control': 'no-store' } },
      );
    }
    const payload = shapePayload(q, event, nowMs);
    cache.set(q, { expiresAt: nowMs + TTL * 1000, payload });
    return Response.json(payload, { headers: { 'Cache-Control': `public, max-age=${TTL}` } });
  } catch (e) {
    return Response.json({ ok: false, error: String(e?.message || e) }, { status: 502 });
  }
}
