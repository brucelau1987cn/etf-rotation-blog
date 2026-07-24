export const BUY_TIME_CYCLES = [
  { code: '2', hours: 2, label: '2小时' },
  { code: '2.5', hours: 2.5, label: '2.5小时' },
  { code: '3', hours: 3, label: '3小时' },
  { code: '3.5', hours: 3.5, label: '3.5小时' },
  { code: '4', hours: 4, label: '4小时' },
  { code: '4.5', hours: 4.5, label: '4.5小时' },
  { code: '5', hours: 5, label: '5小时' },
  { code: '5.5', hours: 5.5, label: '5.5小时' },
  { code: '6', hours: 6, label: '6小时' },
  { code: '6.5', hours: 6.5, label: '6.5小时' },
  { code: '7', hours: 7, label: '7小时' },
  { code: '7.5', hours: 7.5, label: '7.5小时' },
  { code: '8', hours: 8, label: '8小时' }
];

export const SELL_MINUTE_CYCLES = [
  { code: '10m', minutes: 10, label: '10分钟' },
  { code: '30m', minutes: 30, label: '30分钟' },
  { code: '60m', minutes: 60, label: '60分钟' },
  { code: '90m', minutes: 90, label: '90分钟' },
  { code: '120m', minutes: 120, label: '120分钟' },
  { code: '150m', minutes: 150, label: '150分钟' },
  { code: '180m', minutes: 180, label: '180分钟' },
  { code: '210m', minutes: 210, label: '210分钟' },
  { code: '240m', minutes: 240, label: '240分钟' }
];

export function validatePublicPayload(payload) {
  if (!payload || typeof payload !== 'object') throw new Error('payload must be an object');
  if (payload.schema_version !== 'a-rolling-energy-v4') throw new Error('unsupported schema_version');
  if (!Array.isArray(payload.cycles)) throw new Error('cycles must be an array');
  if (!payload.sell_chain || typeof payload.sell_chain !== 'object') throw new Error('sell_chain must be an object');
  if (!payload.transmission || typeof payload.transmission !== 'object') throw new Error('transmission must be an object');
  return payload;
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  if (!upstream || typeof upstream !== 'object') throw new Error('upstream must be an object');

  // 多方买入节点按需投影：有信号则显示，无信号不占位
  const rawCycles = Array.isArray(upstream.cycles) ? upstream.cycles : [];
  const cycles = rawCycles
    .filter(c => c && c.buy_state === 'BUY')
    .map(c => ({
      cycle_code: String(c.cycle_code || c.code),
      hours: parseFloat(c.hours) || parseFloat(c.cycle_code) || 0,
      buy_state: 'BUY',
      buy_triggered_at: c.buy_triggered_at || c.triggered_at || generatedAt,
      label: c.label || `${c.cycle_code}小时`
    }));

  const litCount = cycles.length;
  const currentCycleCode = litCount > 0 ? cycles[litCount - 1].cycle_code : null;
  const startedAt = litCount > 0 ? cycles[0].buy_triggered_at : null;
  const latestTriggeredAt = litCount > 0 ? cycles[litCount - 1].buy_triggered_at : null;

  // 空方卖出节点：必须在多方产生买入信号后才依次显示；无信号不占位
  const rawSellNodes = Array.isArray(upstream.sell_chain?.nodes) ? upstream.sell_chain.nodes : [];
  const sellNodes = litCount > 0 
    ? rawSellNodes
        .filter(n => n && (n.sell_state === 'SELL' || n.sell_state === 'OBSERVING'))
        .map(n => ({
          code: String(n.code || n.cycle_code),
          timeframe_minutes: parseInt(n.timeframe_minutes, 10) || parseInt(n.code, 10) || 0,
          sell_state: n.sell_state,
          triggered_at: n.triggered_at || generatedAt,
          label: n.label || `${n.code}分钟`
        }))
    : [];

  const payload = {
    schema_version: 'a-rolling-energy-v4',
    mode: 'live',
    generated_at: generatedAt,
    data_as_of: upstream.data_as_of || generatedAt,
    freshness: 'fresh',
    stale_after_seconds: staleAfterSeconds,
    notice: '按需点亮；无信号不占位；必须存在多方信号后方出空方信号。',
    delivery: { state: 'live', reason: null },
    instrument: upstream.instrument || { instrument_name: '上海电力', exchange: 'SSE', symbol: '600021' },
    transmission: {
      state: litCount > 0 ? 'transmitting' : 'observing',
      basis: 'dynamic_on_demand',
      current_cycle_code: currentCycleCode,
      started_at: startedAt,
      latest_triggered_at: latestTriggeredAt,
      lit_count: litCount
    },
    cycles,
    sell_chain: {
      nodes: sellNodes
    },
    sell_alerts: upstream.sell_alerts || []
  };

  return validatePublicPayload(payload);
}

export function asLkg(payload, reason) {
  const degraded = JSON.parse(JSON.stringify(payload));
  degraded.delivery = { state: 'lkg', reason: String(reason || '').slice(0, 200) };
  return degraded;
}
