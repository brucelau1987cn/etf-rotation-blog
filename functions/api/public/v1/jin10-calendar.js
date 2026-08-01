const UPSTREAM = 'https://rili-open-api.jin10.com/data/week_info';
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_RANGE_DAYS = 31;

const json = (body, status = 200, cache = 'public, max-age=60, s-maxage=300, stale-while-revalidate=600') => new Response(JSON.stringify(body), {
  status,
  headers: {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': cache,
    'x-content-type-options': 'nosniff',
  },
});

const isRealDate = (value) => {
  if (!DATE_RE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
};

const daysBetween = (start, end) => Math.round((new Date(`${end}T00:00:00Z`) - new Date(`${start}T00:00:00Z`)) / 86400000);

const normalizeItem = (entry) => {
  const type = String(entry?.type || 'unknown');
  const data = entry?.data && typeof entry.data === 'object' ? entry.data : {};
  const time = data.pub_time || data.event_time || data.holiday_date || null;
  const title = data.title_full || data.title || data.indicator_name || data.name || data.event_content || data.summary || data.holiday_name || '未命名事项';
  return {
    type,
    id: data.id ?? data.data_id ?? null,
    indicator_id: data.indicator_id ?? null,
    time,
    country: data.country || data.region || null,
    star: Number.isFinite(Number(data.star)) ? Number(data.star) : null,
    title,
    previous: data.previous ?? null,
    consensus: data.consensus ?? null,
    actual: data.actual ?? null,
    revised: data.revised ?? null,
    unit: data.unit ?? null,
    affect: data.affect ?? null,
    time_status: data.time_status ?? null,
    source: data.source ?? null,
  };
};

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const date = String(url.searchParams.get('date') || '').trim();
  const start = String(url.searchParams.get('start_date') || date || new Date().toISOString().slice(0, 10)).trim();
  const end = String(url.searchParams.get('end_date') || date || start).trim();

  if (!isRealDate(start) || !isRealDate(end)) return json({ error: 'invalid date; expected YYYY-MM-DD' }, 400, 'no-store');
  const span = daysBetween(start, end);
  if (span < 0 || span > MAX_RANGE_DAYS) return json({ error: `date range must be 0-${MAX_RANGE_DAYS} days` }, 400, 'no-store');

  const upstreamUrl = new URL(UPSTREAM);
  upstreamUrl.searchParams.set('start_date', start);
  upstreamUrl.searchParams.set('end_date', end);

  let response;
  try {
    response = await fetch(upstreamUrl.toString(), {
      headers: {
        accept: 'application/json',
        'x-app-id': 'fiXF2nOnDycGutVA',
        'x-version': '2.0',
        'user-agent': 'Mozilla/5.0 ETF-Compass/1.0',
      },
      cf: { cacheTtl: 300, cacheEverything: true },
    });
  } catch {
    return json({ error: 'jin10 upstream unavailable' }, 502, 'no-store');
  }
  if (!response.ok) return json({ error: 'jin10 upstream unavailable', upstream_status: response.status }, 502, 'no-store');

  let upstream;
  try {
    upstream = await response.json();
  } catch {
    return json({ error: 'invalid jin10 response' }, 502, 'no-store');
  }
  if (Number(upstream?.status) !== 200 || !Array.isArray(upstream?.data)) return json({ error: 'invalid jin10 payload' }, 502, 'no-store');

  const items = upstream.data.map(normalizeItem).sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));
  const counts = { data: 0, event: 0, holiday: 0 };
  for (const item of items) {
    if (Object.prototype.hasOwnProperty.call(counts, item.type)) counts[item.type] += 1;
  }
  return json({
    status: 'ok',
    source: '金十财经日历',
    start_date: start,
    end_date: end,
    date: start === end ? start : null,
    count: items.length,
    counts,
    items,
  });
}
