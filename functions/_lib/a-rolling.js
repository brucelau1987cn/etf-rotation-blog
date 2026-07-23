export const CYCLES = [
  ['PRE', '预备', 105],
  ['A1', 'A', 120], ['A2', 'A', 135], ['A3', 'A', 150], ['A4', 'A', 165],
  ['B1', 'B', 180], ['B2', 'B', 195], ['B3', 'B', 210], ['B4', 'B', 225],
  ['C1', 'C', 240], ['C2', 'C', 250], ['C3', 'C', 260], ['C4', 'C', 270], ['C5', 'C', 280], ['C6', 'C', 290],
  ['D1', 'D', 300], ['D2', 'D', 310], ['D3', 'D', 320], ['D4', 'D', 330], ['D5', 'D', 340], ['D6', 'D', 350],
  ['E1', 'E', 360], ['E2', 'E', 370], ['E3', 'E', 380], ['E4', 'E', 390], ['E5', 'E', 400], ['E6', 'E', 410],
  ['F1', 'F', 420], ['F2', 'F', 430], ['F3', 'F', 440], ['F4', 'F', 450], ['F5', 'F', 460], ['F6', 'F', 470],
  ['G', 'G', 480],
];

const ROOT_KEYS = ['schema_version', 'mode', 'generated_at', 'data_as_of', 'freshness', 'stale_after_seconds', 'notice', 'delivery', 'instrument', 'transmission', 'cycles', 'sell_alerts'];
const CYCLE_KEYS = ['cycle_code', 'segment', 'timeframe_minutes', 'timeframe_label', 'buy_state', 'buy_triggered_at', 'source'];
const ALERT_KEYS = ['alert_id', 'cycle_code', 'timeframe_minutes', 'triggered_at', 'transmission_position', 'remaining_session_minutes', 'analysis_state', 'analysis'];
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys) => isObject(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const iso = (value, label) => {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value)) || !/(Z|[+-]\d\d:\d\d)$/.test(value)) throw new Error(`${label} must be a timezone-aware timestamp`);
  return new Date(value).toISOString().replace('.000Z', 'Z');
};
const labelFor = minutes => `${Math.floor(minutes / 60)}小时${minutes % 60 ? `${minutes % 60}分钟` : ''}`;

export function validatePublicPayload(payload) {
  if (!exactKeys(payload, ROOT_KEYS)) throw new Error('public payload has unknown or missing root fields');
  if (payload.schema_version !== 'a-rolling-energy-v2') throw new Error('unsupported schema_version');
  if (!['demo', 'live'].includes(payload.mode)) throw new Error('invalid mode');
  iso(payload.generated_at, 'generated_at');
  iso(payload.data_as_of, 'data_as_of');
  if (!['fresh', 'stale', 'unknown'].includes(payload.freshness)) throw new Error('invalid freshness');
  if (!Number.isInteger(payload.stale_after_seconds) || payload.stale_after_seconds < 60 || payload.stale_after_seconds > 86400) throw new Error('invalid stale threshold');
  if (typeof payload.notice !== 'string' || !payload.notice || payload.notice.length > 300) throw new Error('invalid notice');
  if (!exactKeys(payload.delivery, ['state', 'reason']) || !['live', 'lkg'].includes(payload.delivery.state)) throw new Error('invalid delivery');
  if (payload.delivery.reason !== null && (typeof payload.delivery.reason !== 'string' || payload.delivery.reason.length > 200)) throw new Error('invalid delivery reason');
  if (!exactKeys(payload.instrument, ['instrument_name', 'exchange', 'symbol']) || !['SSE', 'SZSE'].includes(payload.instrument.exchange) || !/^\d{6}$/.test(payload.instrument.symbol) || !payload.instrument.instrument_name) throw new Error('invalid instrument');
  if (!exactKeys(payload.transmission, ['state', 'basis', 'current_cycle_code', 'started_at', 'lit_count', 'continuous_confirmed'])) throw new Error('invalid transmission');
  if (!['observing', 'transmitting', 'stopped', 'complete', 'unknown'].includes(payload.transmission.state) || !['latest_buy_by_cycle', 'single_run'].includes(payload.transmission.basis) || typeof payload.transmission.continuous_confirmed !== 'boolean') throw new Error('invalid transmission state');
  if (!Array.isArray(payload.cycles) || payload.cycles.length !== CYCLES.length) throw new Error('energy contract requires 34 cycles');
  let litCount = 0;
  payload.cycles.forEach((row, index) => {
    const [code, segment, minutes] = CYCLES[index];
    if (!exactKeys(row, CYCLE_KEYS)) throw new Error(`cycle ${index} has unknown or missing fields`);
    if (row.cycle_code !== code || row.segment !== segment || row.timeframe_minutes !== minutes || row.timeframe_label !== labelFor(minutes)) throw new Error(`cycle ${index} does not match the canonical sequence`);
    if (!['BUY', 'INACTIVE', 'UNKNOWN'].includes(row.buy_state) || !['DEMO_FIXTURE', 'UPSTREAM_PROJECTION'].includes(row.source)) throw new Error(`cycle ${index} has invalid enum`);
    if (row.buy_triggered_at !== null) iso(row.buy_triggered_at, `cycle ${index} buy_triggered_at`);
    if (row.buy_state === 'BUY' && row.buy_triggered_at === null) throw new Error(`cycle ${index} BUY requires a trigger time`);
    if (row.buy_state !== 'BUY' && row.buy_triggered_at !== null) throw new Error(`cycle ${index} inactive state cannot have a trigger time`);
    if (row.buy_state === 'BUY') litCount += 1;
  });
  if (!Number.isInteger(payload.transmission.lit_count) || payload.transmission.lit_count !== litCount) throw new Error('transmission lit_count mismatch');
  const current = litCount ? payload.cycles[litCount - 1].cycle_code : null;
  if (payload.cycles.slice(0, litCount).some(row => row.buy_state !== 'BUY') || payload.cycles.slice(litCount).some(row => row.buy_state === 'BUY')) throw new Error('BUY path must be contiguous from PRE');
  if (payload.transmission.current_cycle_code !== current) throw new Error('transmission current_cycle_code mismatch');
  if (payload.transmission.started_at !== null) iso(payload.transmission.started_at, 'transmission.started_at');
  if (!Array.isArray(payload.sell_alerts) || payload.sell_alerts.length > 100) throw new Error('invalid sell alerts');
  payload.sell_alerts.forEach((alert, index) => {
    if (!exactKeys(alert, ALERT_KEYS)) throw new Error(`sell alert ${index} has unknown or missing fields`);
    const cycle = CYCLES.find(item => item[0] === alert.cycle_code);
    if (!cycle || alert.timeframe_minutes !== cycle[2]) throw new Error(`sell alert ${index} has invalid cycle`);
    iso(alert.triggered_at, `sell alert ${index} triggered_at`);
    if (!Number.isInteger(alert.remaining_session_minutes) || alert.remaining_session_minutes < 0 || alert.remaining_session_minutes > 240) throw new Error(`sell alert ${index} has invalid remaining time`);
    if (!['pending', 'ready', 'unavailable'].includes(alert.analysis_state) || typeof alert.analysis !== 'string' || alert.analysis.length > 1200) throw new Error(`sell alert ${index} has invalid analysis`);
  });
  return structuredClone(payload);
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  if (!isObject(upstream) || !Array.isArray(upstream.cycles)) throw new Error('upstream payload requires cycles');
  const rows = new Map(upstream.cycles.map(row => [row.cycle_code, row]));
  if (rows.size !== CYCLES.length) throw new Error('upstream cycle set is incomplete or duplicated');
  const cycles = CYCLES.map(([code, segment, minutes]) => {
    const raw = rows.get(code);
    if (!raw || raw.timeframe_minutes !== minutes) throw new Error(`upstream cycle ${code} is missing or invalid`);
    return { cycle_code: code, segment, timeframe_minutes: minutes, timeframe_label: labelFor(minutes), buy_state: raw.buy_state, buy_triggered_at: raw.buy_triggered_at ? iso(raw.buy_triggered_at, `${code}.buy_triggered_at`) : null, source: 'UPSTREAM_PROJECTION' };
  });
  const litCount = cycles.filter(row => row.buy_state === 'BUY').length;
  if (cycles.slice(0, litCount).some(row => row.buy_state !== 'BUY') || cycles.slice(litCount).some(row => row.buy_state === 'BUY')) throw new Error('upstream BUY path must be contiguous from PRE');
  const dataAsOf = iso(upstream.data_as_of || upstream.generated_at, 'data_as_of');
  const generated = iso(generatedAt, 'generated_at');
  const age = Math.max(0, Math.floor((Date.parse(generated) - Date.parse(dataAsOf)) / 1000));
  const startedAt = litCount ? cycles[0].buy_triggered_at : null;
  return validatePublicPayload({
    schema_version: 'a-rolling-energy-v2', mode: 'live', generated_at: generated, data_as_of: dataAsOf,
    freshness: age <= staleAfterSeconds ? 'fresh' : 'stale', stale_after_seconds: staleAfterSeconds,
    notice: '只读公开投影；买卖信号事实由上游信号系统提供。', delivery: { state: 'live', reason: null },
    instrument: upstream.instrument,
    transmission: { state: litCount === 34 ? 'complete' : litCount ? 'transmitting' : 'observing', basis: 'single_run', current_cycle_code: litCount ? cycles[litCount - 1].cycle_code : null, started_at: startedAt, lit_count: litCount, continuous_confirmed: true },
    cycles, sell_alerts: Array.isArray(upstream.sell_alerts) ? upstream.sell_alerts : [],
  });
}

export function asLkg(payload, reason) {
  const result = validatePublicPayload(payload);
  result.delivery = { state: 'lkg', reason: String(reason || 'upstream unavailable').slice(0, 200) };
  return validatePublicPayload(result);
}
