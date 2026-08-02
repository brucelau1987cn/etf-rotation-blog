/**
 * Jin10 indicator history proxy (rili-open-api.jin10.com).
 *
 * GET /api/public/v1/jin10-indicator-history?id=75&months=2
 *
 * Returns the last N months of previous/consensus/actual for one indicator,
 * plus the next scheduled release (looked up from the calendar API by title).
 */
const UPSTREAM = 'https://rili-open-api.jin10.com/getDataByIndIdAndDateRange';
const CALENDAR_API = 'https://etf.peekabo.cc/api/public/v1/jin10-calendar';

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=1800' },
});

const upstreamHeaders = (env) => ({
  'x-token': env.JIN10_X_TOKEN || '5c5b9d34-0899-441c-9cb6-60f58a0ec731',
  'x-version': '2.0',
  'x-app-id': env.JIN10_X_APP_ID || 'fiXF2nOnDycGutVA',
  'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Html5Plus/1.0 (Immersed/59) uni-app',
  'accept': '*/*',
  'accept-language': 'zh-CN,zh-Hans;q=0.9',
});

// id → { label, titleMatch } where titleMatch is a substring used to find the
// next release via calendar API.
const INDICATORS = {
  75: { label: '美国非农就业人数', unit: '万人', titleMatch: '非农' },
  76: { label: '美国失业率', unit: '%', titleMatch: '失业率' },
  78: { label: '美国ADP就业人数', unit: '万人', titleMatch: 'ADP' },
  194: { label: '美国零售销售月率', unit: '%', titleMatch: '零售销售' },
  211: { label: '美国核心PCE物价指数年率', unit: '%', titleMatch: '核心PCE' },
  214: { label: '美国核心PCE物价指数月率', unit: '%', titleMatch: '核心PCE' },
  232: { label: '美国未季调核心CPI年率', unit: '%', titleMatch: '核心CPI' },
  233: { label: '美国季调后核心CPI月率', unit: '%', titleMatch: '核心CPI' },
  234: { label: '美国未季调CPI年率', unit: '%', titleMatch: 'CPI' },
  230: { label: '美国季调后CPI月率', unit: '%', titleMatch: 'CPI' },
};

const parseRows = (unit, key, vals) => {
  const idx = (name) => (key || []).findIndex((k) => k.includes(name));
  const iPrev = idx('前值');
  const iCons = idx('预期');
  const iAct = idx('公布');
  const iTime = idx('时间');
  const iPeriod = idx('时间区间');
  return (vals || [])
    .map((v) => {
      const parts = String(v).split(',');
      if (parts.length < 5) return null;
      const num = (s) => (s === '' || s === null || s === undefined ? null : Number(s));
      return {
        period: (parts[iPeriod] ?? parts[4] ?? '').trim(),
        previous: num(parts[iPrev] ?? parts[0]),
        consensus: num(parts[iCons] ?? parts[1]),
        actual: num(parts[iAct] ?? parts[2]),
        published_at: parts[iTime] ?? parts[3] ?? null,
        unit,
      };
    })
    .filter(Boolean);
};

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const id = Number(url.searchParams.get('id')) || 75;
  const meta = INDICATORS[id];
  if (!meta) return json({ error: `unsupported indicator id=${id}`, supported: Object.keys(INDICATORS), source: 'jin10-indicator-history' }, 400);

  const months = Math.max(1, Math.min(12, Number(url.searchParams.get('months')) || 2));
  const now = new Date();
  const dateStart = new Date(now);
  dateStart.setMonth(dateStart.getMonth() - months - 2); // pull extra, slice later
  const dateEnd = new Date(now);
  dateEnd.setDate(dateEnd.getDate() + 2);
  const iso = (d) => d.toISOString().slice(0, 10);
  const qs = new URLSearchParams({ category: '', id: String(id), dateRange: `${iso(dateStart)},${iso(dateEnd)}` });

  try {
    const resp = await fetch(`${UPSTREAM}?${qs}`, { headers: upstreamHeaders(env) });
    if (!resp.ok) return json({ error: `upstream HTTP ${resp.status}`, source: 'jin10-indicator-history' }, 502);
    const upstream = await resp.json();
    const data = upstream?.data || {};
    const rows = parseRows(data.unit, data.key, data.val).slice(-months);

    // Next scheduled release: look up the actual next release from the Jin10
    // calendar API for this indicator (searches the next 45 days).
    let next_release = null;
    try {
      const CALENDAR_API = 'https://etf.peekabo.cc/api/public/v1/jin10-calendar';
      for (let offset = 1; offset <= 45; offset++) {
        const d = new Date(now);
        d.setDate(d.getDate() + offset);
        const dateStr = iso(d);
        const calResp = await fetch(`${CALENDAR_API}?date=${dateStr}`, { headers: { 'User-Agent': 'ETF-Compass-Macro/1.0' } });
        if (!calResp.ok) continue;
        const calPayload = await calResp.json();
        const calItems = calPayload?.items || calPayload?.data?.items || [];
        for (const item of calItems) {
          const title = String(item?.title || '').replace(/\s+/g, '');
          // Prefer exact indicator_id match; fall back to title substring.
          const idMatch = Number(item?.indicator_id) === id;
          const titleMatch = title.includes(meta.titleMatch);
          if (!idMatch && !titleMatch) continue;
          const time = item?.time || item?.date || item?.show_time || '';
          if (!time) continue;
          next_release = {
            time: String(time).replace('T', ' ').slice(0, 16),
            title: item?.title || meta.label,
            star: Number(item?.star) || 0,
            previous: item?.previous ?? null,
            consensus: item?.consensus ?? null,
            actual: item?.actual ?? null,
          };
          break;
        }
        if (next_release) break;
      }
    } catch (_) { /* optional */ }
    // Fallback: first Friday rule (nonfarm payroll month pattern).
    if (!next_release) {
      for (let m = 0; m < 3; m++) {
        const d = new Date(now.getFullYear(), now.getMonth() + m, 1);
        while (d.getDay() !== 5) d.setDate(d.getDate() + 1);
        const dateStr = iso(d);
        if (dateStr <= iso(now)) continue;
        next_release = { time: `${dateStr} 20:30`, title: meta.label, star: 5, previous: rows[0]?.actual ?? null, consensus: null, actual: null };
        break;
      }
    }

    return json({
      status: 'ok',
      source: 'jin10-indicator-history',
      id,
      label: meta.label,
      unit: data.unit || meta.unit,
      months,
      next_release,
      rows,
    });
  } catch (error) {
    return json({ error: 'upstream fetch failed', detail: error.message, source: 'jin10-indicator-history' }, 502);
  }
}