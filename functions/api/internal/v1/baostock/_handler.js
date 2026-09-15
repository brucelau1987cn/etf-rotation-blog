import { baoStockSecCode, fetchCalendarFromBaoStock, fetchKlineFromBaoStock } from '../../../public/v1/_baostock.js';

const MAX_SYMBOLS = 5;
const MAX_DATE_SPAN_DAYS = 366;
const DEFAULT_FIELDS = ['date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'tradestatus'];
const ALLOWED_FIELDS = new Set(DEFAULT_FIELDS);
const SYMBOL_RE = /^(?:(?:sh|sz)\.\d{6}|\d{6}(?:\.(?:sh|sz))?)$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function headers(cacheControl = 'no-store') {
  return {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': cacheControl,
    'x-content-type-options': 'nosniff',
  };
}

function reply(payload, status = 200, cacheControl = 'no-store') {
  return new Response(JSON.stringify(payload), { status, headers: headers(cacheControl) });
}

function fail(code, message, status) {
  return reply({ ok: false, code, message }, status);
}

function validDate(value) {
  if (!DATE_RE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function dateRange(url, defaultDays = 30) {
  const end = url.searchParams.get('end') || new Date().toISOString().slice(0, 10);
  if (!validDate(end)) return null;
  const start = url.searchParams.get('start') || new Date(Date.parse(`${end}T00:00:00Z`) - (defaultDays - 1) * 86400000).toISOString().slice(0, 10);
  if (!validDate(start)) return null;
  const spanDaysInclusive = Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000) + 1;
  if (spanDaysInclusive < 1 || spanDaysInclusive > MAX_DATE_SPAN_DAYS) return null;
  return { start, end };
}

function parseSymbols(url) {
  const raw = url.searchParams.get('symbols') || url.searchParams.get('symbol') || '';
  const symbols = raw.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean);
  if (!symbols.length || symbols.length > MAX_SYMBOLS || symbols.some((symbol) => !SYMBOL_RE.test(symbol))) return null;
  const normalized = symbols.map(baoStockSecCode);
  return new Set(normalized).size === normalized.length ? normalized : null;
}

function parseFields(url) {
  const raw = url.searchParams.get('fields');
  const fields = raw ? raw.split(',').map((value) => value.trim()).filter(Boolean) : DEFAULT_FIELDS;
  if (!fields.length || !fields.includes('date') || fields.some((field) => !ALLOWED_FIELDS.has(field)) || new Set(fields).size !== fields.length) return null;
  return fields;
}

function assertRows(rows, label) {
  if (!Array.isArray(rows)) throw new Error(`invalid ${label} rows`);
  return rows;
}

function assertAscendingDate(date, previousDate, range, label) {
  if (!validDate(date) || date < range.start || date > range.end) throw new Error(`invalid ${label} date`);
  if (previousDate !== null && date <= previousDate) throw new Error(`unordered ${label} date`);
}

function normalizeCalendarRecords(rows, range) {
  let previousDate = null;
  return assertRows(rows, 'calendar').map((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('invalid calendar row');
    const date = row.calendar_date;
    assertAscendingDate(date, previousDate, range, 'calendar');
    if (row.is_trading_day !== '0' && row.is_trading_day !== '1'
        && row.is_trading_day !== 0 && row.is_trading_day !== 1) {
      throw new Error('invalid is_trading_day');
    }
    previousDate = date;
    return { date, is_trading_day: String(row.is_trading_day) === '1' };
  });
}

function normalizeKlineRecords(rows, fields, range) {
  let previousDate = null;
  return assertRows(rows, 'qfq').map((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('invalid qfq row');
    if (fields.some((field) => !Object.hasOwn(row, field))) throw new Error('missing requested field');

    const date = row.date;
    assertAscendingDate(date, previousDate, range, 'qfq');
    previousDate = date;

    if (Object.hasOwn(row, 'tradestatus') && row.tradestatus !== '0' && row.tradestatus !== '1'
        && row.tradestatus !== 0 && row.tradestatus !== 1) {
      throw new Error('invalid tradestatus');
    }

    const result = { ...row };
    for (const field of ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn']) {
      if (!Object.hasOwn(result, field)) continue;
      if ((typeof result[field] === 'string' && result[field].trim() === '')
          || result[field] === null || typeof result[field] === 'boolean') throw new Error(`invalid ${field}`);
      const number = Number(result[field]);
      if (!Number.isFinite(number)) throw new Error(`invalid ${field}`);
      result[field] = number;
    }

    if (['open', 'high', 'low', 'close'].every((field) => fields.includes(field))) {
      const { open, high, low, close } = result;
      if (low > high || high < Math.max(open, close) || low > Math.min(open, close)) throw new Error('invalid OHLC relationship');
    }
    return result;
  });
}

export async function handleBaoStockInternal({ request, env }, dependencies = {}) {
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: { ...headers(), allow: 'GET, OPTIONS' } });
  }
  if (request.method !== 'GET') return fail('METHOD_NOT_ALLOWED', 'GET required', 405);

  const expected = String(env?.BAOSTOCK_API_TOKEN || '').trim();
  if (!expected) return fail('UNAVAILABLE', 'BaoStock internal API is not configured', 503);
  if ((request.headers.get('authorization') || '') !== `Bearer ${expected}`) {
    return fail('UNAUTHORIZED', 'valid bearer token required', 401);
  }

  const url = new URL(request.url);
  const route = url.pathname.replace(/^.*\/api\/internal\/v1\/baostock\/?/, '').replace(/\/$/, '');
  if (!['calendar', 'qfq'].includes(route)) return fail('NOT_FOUND', 'supported routes: calendar, qfq', 404);
  const range = dateRange(url);
  if (!range) return fail('BAD_REQUEST', `valid start/end within ${MAX_DATE_SPAN_DAYS} days required`, 400);

  try {
    if (route === 'calendar') {
      const fetchCalendarImpl = dependencies.fetchCalendarImpl || fetchCalendarFromBaoStock;
      const records = normalizeCalendarRecords(await fetchCalendarImpl(range.start, range.end), range);
      return reply({ ok: true, source: 'baostock', ...range, count: records.length, records }, 200,
        'private, max-age=3600, stale-while-revalidate=86400');
    }

    if (route === 'qfq') {
      const symbols = parseSymbols(url);
      const fields = parseFields(url);
      if (!symbols || !fields) return fail('BAD_REQUEST', `1-${MAX_SYMBOLS} unique A-share symbols and supported OHLCV fields required`, 400);
      const fetchKlineImpl = dependencies.fetchKlineImpl || fetchKlineFromBaoStock;
      const results = await Promise.all(symbols.map(async (symbol) => {
        const records = normalizeKlineRecords(await fetchKlineImpl(symbol, { adjust: 'qfq', ...range, fields }), fields, range);
        return { symbol, count: records.length, records };
      }));
      const count = results.reduce((sum, result) => sum + result.count, 0);
      return reply({ ok: true, source: 'baostock', adjust: 'qfq', ...range, fields, symbol_count: results.length, count, results }, 200,
        'private, max-age=300, stale-while-revalidate=1800');
    }

    return fail('NOT_FOUND', 'supported routes: calendar, qfq', 404);
  } catch (error) {
    console.error(JSON.stringify({ event: 'baostock_internal_error', route, message: error?.message || 'unknown' }));
    return fail('UPSTREAM_UNAVAILABLE', 'BaoStock data unavailable', 502);
  }
}

export { ALLOWED_FIELDS, DEFAULT_FIELDS, MAX_DATE_SPAN_DAYS, MAX_SYMBOLS };
