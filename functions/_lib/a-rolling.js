export const CANONICAL_CYCLES = [
  ['PRE', '预备', 105, '1小时45分钟'],
  ['A1', 'A', 120, '2小时'],
  ['A2', 'A', 135, '2小时15分钟'],
  ['A3', 'A', 150, '2小时30分钟'],
  ['A4', 'A', 165, '2小时45分钟'],
  ['B1', 'B', 180, '3小时'],
  ['B2', 'B', 195, '3小时15分钟'],
  ['B3', 'B', 210, '3小时30分钟'],
  ['B4', 'B', 225, '3小时45分钟'],
  ['C1', 'C', 240, '4小时'],
  ['C2', 'C', 250, '4小时10分钟'],
  ['C3', 'C', 260, '4小时20分钟'],
  ['C4', 'C', 270, '4小时30分钟'],
  ['C5', 'C', 280, '4小时40分钟'],
  ['C6', 'C', 290, '4小时50分钟'],
  ['D1', 'D', 300, '5小时'],
  ['D2', 'D', 310, '5小时10分钟'],
  ['D3', 'D', 320, '5小时20分钟'],
  ['D4', 'D', 330, '5小时30分钟'],
  ['D5', 'D', 340, '5小时40分钟'],
  ['D6', 'D', 350, '5小时50分钟'],
  ['E1', 'E', 360, '6小时'],
  ['E2', 'E', 370, '6小时10分钟'],
  ['E3', 'E', 380, '6小时20分钟'],
  ['E4', 'E', 390, '6小时30分钟'],
  ['E5', 'E', 400, '6小时40分钟'],
  ['E6', 'E', 410, '6小时50分钟'],
  ['F1', 'F', 420, '7小时'],
  ['F2', 'F', 430, '7小时10分钟'],
  ['F3', 'F', 440, '7小时20分钟'],
  ['F4', 'F', 450, '7小时30分钟'],
  ['F5', 'F', 460, '7小时40分钟'],
  ['F6', 'F', 470, '7小时50分钟'],
  ['G', 'G', 480, '8小时']
];

export function validatePublicPayload(payload) {
  if (!payload || typeof payload !== 'object') throw new Error('payload must be an object');
  if (payload.schema_version !== 'a-rolling-energy-v3') throw new Error('unsupported schema_version');
  if (!Array.isArray(payload.cycles) || payload.cycles.length !== 34) throw new Error('cycles must be a 34-item array');
  if (!payload.sell_chain || typeof payload.sell_chain !== 'object') throw new Error('sell_chain must be an object');
  if (!payload.transmission || typeof payload.transmission !== 'object') throw new Error('transmission must be an object');
  return payload;
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  if (!upstream || typeof upstream !== 'object') throw new Error('upstream must be an object');
  if (!Array.isArray(upstream.cycles) || upstream.cycles.length !== 34) throw new Error('upstream cycles must be a 34-item array');
  
  const cycles = CANONICAL_CYCLES.map(([code, segment, minutes, label], index) => {
    const raw = upstream.cycles[index] || {};
    const state = raw.buy_state || 'INACTIVE';
    const triggered = raw.buy_triggered_at || null;
    return {
      cycle_code: code,
      segment,
      timeframe_minutes: minutes,
      timeframe_label: label,
      buy_state: state,
      buy_triggered_at: triggered,
      source: 'UPSTREAM_PROJECTION'
    };
  });

  const litCount = cycles.filter(c => c.buy_state === 'BUY').length;
  for (let i = 0; i < litCount; i++) {
    if (cycles[i].buy_state !== 'BUY') throw new Error('upstream BUY path must be contiguous from PRE');
  }
  for (let i = litCount; i < cycles.length; i++) {
    if (cycles[i].buy_state === 'BUY') throw new Error('upstream BUY path must be contiguous from PRE');
  }

  const stoppedAtCode = litCount < cycles.length ? cycles[litCount].cycle_code : null;
  const currentCycleCode = litCount > 0 ? cycles[litCount - 1].cycle_code : null;
  const startedAt = litCount > 0 ? cycles[0].buy_triggered_at : null;
  const latestTriggeredAt = litCount > 0 ? cycles[litCount - 1].buy_triggered_at : null;

  const payload = {
    schema_version: 'a-rolling-energy-v3',
    mode: 'live',
    generated_at: generatedAt,
    data_as_of: upstream.data_as_of || generatedAt,
    freshness: 'fresh',
    stale_after_seconds: staleAfterSeconds,
    notice: '只读公开投影；买卖信号事实由上游信号系统提供。',
    delivery: { state: 'live', reason: null },
    instrument: upstream.instrument || { instrument_name: '上海电力', exchange: 'SSE', symbol: '600021' },
    transmission: {
      state: litCount === cycles.length ? 'complete' : litCount > 0 ? 'transmitting' : 'observing',
      basis: 'single_run',
      current_cycle_code: currentCycleCode,
      stopped_at_code: stoppedAtCode,
      started_at: startedAt,
      latest_triggered_at: latestTriggeredAt,
      lit_count: litCount,
      continuous_confirmed: true
    },
    cycles,
    sell_chain: upstream.sell_chain || {
      window_type: 'none',
      window_description: '买方尚未触发预警窗口',
      warning_star: { code: '★', timeframe_minutes: 8, timeframe_label: '8分钟预警', sell_state: 'INACTIVE', triggered_at: null },
      nodes: [
        { code: 'Ⅰ', timeframe_minutes: 10, timeframe_label: '10分钟', sell_state: 'INACTIVE', triggered_at: null },
        { code: 'Ⅱ', timeframe_minutes: 15, timeframe_label: '15分钟', sell_state: 'INACTIVE', triggered_at: null },
        { code: 'Ⅲ', timeframe_minutes: 30, timeframe_label: '30分钟', sell_state: 'INACTIVE', triggered_at: null },
        { code: 'Ⅳ', timeframe_minutes: 45, timeframe_label: '45分钟', sell_state: 'INACTIVE', triggered_at: null },
        { code: 'Ⅴ', timeframe_minutes: 60, timeframe_label: '1小时', sell_state: 'INACTIVE', triggered_at: null }
      ]
    },
    sell_alerts: upstream.sell_alerts || []
  };

  return validatePublicPayload(payload);
}

export function asLkg(payload, reason) {
  const degraded = JSON.parse(JSON.stringify(payload));
  degraded.delivery = { state: 'lkg', reason: String(reason || '').slice(0, 200) };
  return validatePublicPayload(degraded);
}
