export const TIMEFRAMES = ['10m', '30m', '1h', '2h', '3h', '4h', '5h', '6h', '1D'];
const DIRECTIONS = new Set(['BUY', 'SELL', 'UNKNOWN', 'CONFLICT']);
const EXCHANGES = new Set(['SSE', 'SZSE']);
const SOURCES = new Set(['DEMO_FIXTURE', 'UPSTREAM_PROJECTION']);
const SIGNAL_FIELDS = [
  'instrument_name', 'exchange', 'symbol', 'timeframe', 'direction', 'latest_signal_at',
  'duration', 'phase_code', 'phase_label', 'alert_configured_count', 'live_verified_count',
];

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys) => {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
};
const iso = (value, label) => {
  if (typeof value !== 'string' || !value || !Number.isFinite(Date.parse(value)) || !/(Z|[+-]\d\d:\d\d)$/.test(value)) {
    throw new Error(`${label} must be a timezone-aware ISO timestamp`);
  }
  return new Date(value).toISOString().replace('.000Z', 'Z');
};

export function validatePublicPayload(payload) {
  const rootKeys = ['schema_version', 'mode', 'generated_at', 'data_as_of', 'freshness', 'stale_after_seconds', 'notice', 'delivery', 'signals'];
  if (!isObject(payload) || !exactKeys(payload, rootKeys)) throw new Error('public payload has unknown or missing root fields');
  if (payload.schema_version !== 'a-rolling-public-v1') throw new Error('unsupported schema_version');
  if (!['demo', 'live'].includes(payload.mode)) throw new Error('invalid mode');
  iso(payload.generated_at, 'generated_at');
  iso(payload.data_as_of, 'data_as_of');
  if (!['fresh', 'stale', 'unknown'].includes(payload.freshness)) throw new Error('invalid freshness');
  if (!Number.isInteger(payload.stale_after_seconds) || payload.stale_after_seconds < 60 || payload.stale_after_seconds > 86400) throw new Error('invalid stale_after_seconds');
  if (typeof payload.notice !== 'string' || !payload.notice || payload.notice.length > 300) throw new Error('invalid notice');
  if (!isObject(payload.delivery) || !exactKeys(payload.delivery, ['state', 'reason'])) throw new Error('invalid delivery');
  if (!['live', 'lkg'].includes(payload.delivery.state)) throw new Error('invalid delivery state');
  if (payload.delivery.reason !== null && (typeof payload.delivery.reason !== 'string' || payload.delivery.reason.length > 200)) throw new Error('invalid delivery reason');
  if (!Array.isArray(payload.signals) || payload.signals.length !== TIMEFRAMES.length) throw new Error('public payload requires nine signals');
  const signalKeys = [...SIGNAL_FIELDS, 'source'];
  const identities = new Set();
  payload.signals.forEach((row, index) => {
    if (!isObject(row) || !exactKeys(row, signalKeys)) throw new Error(`signal ${index} has unknown or missing fields`);
    if (row.timeframe !== TIMEFRAMES[index]) throw new Error('signals must use the ordered unique timeframe set');
    if (!DIRECTIONS.has(row.direction) || !EXCHANGES.has(row.exchange) || !SOURCES.has(row.source)) throw new Error(`signal ${index} has invalid enum`);
    if (!/^\d{6}$/.test(row.symbol) || typeof row.instrument_name !== 'string' || !row.instrument_name) throw new Error(`signal ${index} has invalid identity`);
    identities.add(`${row.exchange}:${row.symbol}:${row.instrument_name}`);
    if (row.latest_signal_at !== null) iso(row.latest_signal_at, `signal ${index} latest_signal_at`);
    if (typeof row.duration !== 'string' || !row.duration || typeof row.phase_code !== 'string' || !/^[A-Z0-9_]{3,80}$/.test(row.phase_code) || typeof row.phase_label !== 'string' || !row.phase_label) throw new Error(`signal ${index} has invalid text fields`);
    if (!Number.isInteger(row.alert_configured_count) || row.alert_configured_count < 0 || !Number.isInteger(row.live_verified_count) || row.live_verified_count < 0 || row.live_verified_count > row.alert_configured_count) throw new Error(`signal ${index} has invalid counts`);
  });
  if (identities.size !== 1) throw new Error('signals must describe one instrument');
  return structuredClone(payload);
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  if (!isObject(upstream) || !Array.isArray(upstream.signals)) throw new Error('upstream payload requires signals');
  const byTimeframe = new Map();
  for (const [index, raw] of upstream.signals.entries()) {
    if (!isObject(raw) || !TIMEFRAMES.includes(raw.timeframe) || byTimeframe.has(raw.timeframe)) throw new Error(`invalid or duplicate upstream timeframe at ${index}`);
    for (const field of SIGNAL_FIELDS) if (!(field in raw)) throw new Error(`upstream signal ${index} missing ${field}`);
    const row = Object.fromEntries(SIGNAL_FIELDS.map(field => [field, raw[field]]));
    row.direction = String(row.direction).toUpperCase();
    row.source = 'UPSTREAM_PROJECTION';
    if (row.latest_signal_at !== null) row.latest_signal_at = iso(row.latest_signal_at, `signal ${index} latest_signal_at`);
    byTimeframe.set(row.timeframe, row);
  }
  if (byTimeframe.size !== TIMEFRAMES.length) throw new Error('upstream payload is incomplete');
  const dataAsOf = upstream.data_as_of || upstream.generated_at;
  const generatedIso = iso(generatedAt, 'generated_at');
  const dataIso = iso(dataAsOf, 'data_as_of');
  const ageSeconds = Math.max(0, Math.floor((Date.parse(generatedIso) - Date.parse(dataIso)) / 1000));
  return validatePublicPayload({
    schema_version: 'a-rolling-public-v1', mode: 'live', generated_at: generatedIso,
    data_as_of: dataIso, freshness: ageSeconds <= staleAfterSeconds ? 'fresh' : 'stale',
    stale_after_seconds: staleAfterSeconds,
    notice: '只读公开投影；方向与阶段由上游信号系统提供。',
    delivery: { state: 'live', reason: null },
    signals: TIMEFRAMES.map(timeframe => byTimeframe.get(timeframe)),
  });
}

export function asLkg(payload, reason) {
  const result = validatePublicPayload(payload);
  result.delivery = { state: 'lkg', reason: String(reason || 'upstream unavailable').slice(0, 200) };
  return validatePublicPayload(result);
}
