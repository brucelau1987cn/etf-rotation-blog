const THS_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
  Accept: 'application/json,text/plain,*/*',
  Referer: 'https://stockpage.10jqka.com.cn/',
};

const DQ_BASE = 'https://dq.10jqka.com.cn/fuyao/chip_shape_stock_selection';
const D_BASE = 'https://d.10jqka.com.cn/v6/line';
const CODE_RE = /^\d{6}$/;
const KLINE_CODE_RE = /^(?:17|33)_\d{6}$/;
const PERIOD_RE = /^(?:last|20\d{2})$/;

function headers(ttl = 0) {
  return {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'access-control-allow-origin': '*',
    'x-content-type-options': 'nosniff',
  };
}

function reply(payload, status = 200, ttl = 0) {
  return new Response(JSON.stringify(payload), { status, headers: headers(ttl) });
}

function fail(code, message, status) {
  return reply({ ok: false, code, message }, status);
}

function authorized(request, env) {
  const expected = String(env?.THS_API_TOKEN || '');
  if (!expected) return null;
  const supplied = request.headers.get('authorization') || '';
  return supplied === `Bearer ${expected}`;
}

async function upstream(url, ttl = 0) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, {
      headers: THS_HEADERS,
      signal: controller.signal,
      cf: ttl > 0 ? { cacheTtl: ttl, cacheEverything: true } : undefined,
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`upstream HTTP ${response.status}`);
    return text;
  } finally {
    clearTimeout(timer);
  }
}

function parseJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function parseJsonp(text) {
  const match = String(text).match(/^[^(]*\((.*)\)\s*;?\s*$/s);
  return match ? parseJson(match[1]) : null;
}

function parseKlineRecords(raw) {
  return String(raw || '').split(';').filter(Boolean).map((record) => {
    const fields = record.includes('~') ? record.split('~') : record.split(',');
    const number = (index) => {
      const value = Number(fields[index]);
      return Number.isFinite(value) ? value : null;
    };
    const row = {
      date: fields[0] || null,
      open: number(1), high: number(2), low: number(3), close: number(4),
      volume_hands: number(5), amount: number(6), turnover_percent: number(7),
    };
    return row.date && row.open != null && row.high != null && row.low != null && row.close != null ? row : null;
  }).filter(Boolean);
}

function nullableNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function shanghaiDay(offsetDays = 0) {
  const value = new Date(Date.now() + 8 * 3600e3 + offsetDays * 86400e3);
  return value.toISOString().slice(0, 10);
}

function dayTimestamp(day) {
  return String(Date.parse(`${day}T00:00:00+08:00`));
}

export async function handleThsInternal({ request, env }) {
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: { ...headers(), 'access-control-allow-methods': 'GET, OPTIONS', 'access-control-allow-headers': 'Authorization, Content-Type' } });
  }
  if (request.method !== 'GET') return fail('METHOD_NOT_ALLOWED', 'GET required', 405);
  const auth = authorized(request, env);
  if (auth === null) return fail('UNAVAILABLE', 'THS internal API is not configured', 503);
  if (!auth) return fail('UNAUTHORIZED', 'valid bearer token required', 401);

  const url = new URL(request.url);
  const route = url.pathname.replace(/^.*\/api\/internal\/v1\/ths\/?/, '');
  const param = (name, fallback = '') => url.searchParams.get(name) ?? fallback;

  try {
    if (route === 'kline') {
      const code = param('code');
      let period = param('period', 'last');
      if (!KLINE_CODE_RE.test(code) || !PERIOD_RE.test(period)) {
        return fail('BAD_REQUEST', 'code or period is invalid', 400);
      }
      if (period === 'last' && /^(?:17|33)_/.test(code)) period = String(new Date().getUTCFullYear());
      const text = await upstream(`${D_BASE}/${code}/01/${period}.js`, 120);
      const data = parseJsonp(text);
      if (!data) return fail('UPSTREAM_INVALID', 'THS kline parse failed', 502);
      const records = parseKlineRecords(data.data);
      return reply({ ok: true, source: 'd.10jqka.com.cn', code, period, name: data.name || null, count: records.length, records }, 200, 120);
    }

    if (route === 'chip-list') {
      const code = param('code');
      const market = param('market');
      const daysRaw = param('days', '90');
      if (!CODE_RE.test(code) || !['17', '33'].includes(market) || !/^\d{1,3}$/.test(daysRaw)) {
        return fail('BAD_REQUEST', 'code, market or days is invalid', 400);
      }
      const days = Math.min(365, Math.max(1, Number(daysRaw)));
      const end = shanghaiDay();
      const start = shanghaiDay(-days);
      const target = `${DQ_BASE}/stock/v1/chip_list?chip_type=all&stock_code=${code}&stock_market=${market}&start_date=${dayTimestamp(start)}&end_date=${dayTimestamp(end)}`;
      const body = parseJson(await upstream(target, 300));
      if (!body || body.status_code !== 0) return fail('UPSTREAM_INVALID', 'THS chip response invalid', 502);
      const entries = body.data?.list || {};
      const dates = Object.keys(entries).sort();
      const lastDate = dates.at(-1) || null;
      const latest = lastDate ? entries[lastDate] : null;
      return reply({
        ok: true, source: 'dq.10jqka.com.cn', code, market, count: dates.length,
        dates, last_date: lastDate, summary: latest?.summary || null,
        curve: latest?.curve_data?.list || [], data: entries,
      }, 200, 300);
    }

    if (route === 'mainflow') {
      const code = param('code');
      const market = param('market');
      if (!CODE_RE.test(code) || !['17', '33'].includes(market)) {
        return fail('BAD_REQUEST', 'code or market is invalid', 400);
      }
      const target = `https://l2.10jqka.com.cn/eachtradedata/capital/mainflow?marketcode=${market}&stock=${code}`;
      const body = parseJson(await upstream(target, 60));
      if (!body || body.code !== 0) return fail('UPSTREAM_INVALID', 'THS mainflow response invalid', 502);
      const data = body.data || {};
      const cost = nullableNumber(data.mainHoldCostAvgPrice);
      const close = nullableNumber(data.closePrice);
      const ratio = nullableNumber(data.mainHoldCostProfitRatio);
      return reply({
        ok: true, source: 'l2.10jqka.com.cn', code, market, date: data.date || null,
        main_cost: cost != null && cost > 0 ? +cost.toFixed(3) : null,
        main_profit_ratio: ratio != null && ratio >= 0 && ratio <= 1 ? +(ratio * 100).toFixed(2) : null,
        close_price: close != null && close > 0 ? close : null,
        main_avg_profit_pct: cost != null && cost > 0 && close != null && close > 0
          ? +(((close - cost) / cost) * 100).toFixed(2) : null,
        new40: data.new40 ?? null,
      }, 200, 60);
    }

    if (route === 'capital-tab') {
      const code = param('code');
      if (!CODE_RE.test(code)) return fail('BAD_REQUEST', 'code is invalid', 400);
      const target = `https://eq.10jqka.com.cn/fenshiCapitalTab/Public/data/${code}/lhb_0,rzlx_1,dzjy_0.txt`;
      const body = parseJson(await upstream(target, 300));
      if (!body || !['ok', 'success'].includes(String(body.status_msg || '').toLowerCase())) return fail('UPSTREAM_INVALID', 'THS capital response invalid', 502);
      const lhb = body.lhb || {};
      const rzlx = body.rzlx || {};
      const dzjy = body.dzjy || {};
      const top = (value) => (value?.list || []).slice(0, 3).map((row) => ({ name: row.name, buy: row.buy_turnover, sale: row.sale_turnover }));
      return reply({
        ok: true, source: 'eq.10jqka.com.cn', code,
        lhb: { date: lhb.date || null, recent_record_count: lhb.recent_record_count ?? null, net_inflow: lhb.net_inflow ?? null, sale_top: top(lhb.sale_yyb), buy_top: top(lhb.buy_yyb) },
        rzlx: { date: rzlx.date || null, recent_net_inflow: rzlx.recent_net_inflow ?? null, net_3d: rzlx.net_inflow_list?.day_3 ?? null, net_5d: rzlx.net_inflow_list?.day_5 ?? null, net_20d: rzlx.net_inflow_list?.day_20 ?? null, net_60d: rzlx.net_inflow_list?.day_60 ?? null, chart: rzlx.chart || [] },
        dzjy: { list: dzjy.list || [], last_day: dzjy.last_day || null },
      }, 200, 300);
    }

    return fail('NOT_FOUND', 'supported routes: kline, chip-list, mainflow, capital-tab', 404);
  } catch (error) {
    console.error(JSON.stringify({ event: 'ths_internal_error', route, message: error?.message || 'unknown' }));
    return fail('UPSTREAM_UNAVAILABLE', 'THS data unavailable', 502);
  }
}
