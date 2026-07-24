export function validatePublicPayload(payload) {
  if (!payload || typeof payload !== 'object') throw new Error('payload must be an object');
  if (payload.schema_version !== 'a-rolling-energy-v4') throw new Error('unsupported schema_version');
  if (!Array.isArray(payload.timeline)) throw new Error('timeline must be an array');
  if (!payload.transmission || typeof payload.transmission !== 'object') throw new Error('transmission must be an object');
  return payload;
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  if (!upstream || typeof upstream !== 'object') throw new Error('upstream must be an object');

  // 支持通用按接收时间平铺的时序序列 (timeline)
  let rawTimeline = Array.isArray(upstream.timeline) ? upstream.timeline : [];
  
  if (rawTimeline.length === 0) {
    // 从旧结构降级聚合为统一 timeline 顺序
    const rawCycles = Array.isArray(upstream.cycles) ? upstream.cycles : [];
    const rawSellNodes = Array.isArray(upstream.sell_chain?.nodes) ? upstream.sell_chain.nodes : [];

    const buyItems = rawCycles
      .filter(c => c && (c.buy_state === 'BUY' || c.state === 'BUY'))
      .map(c => ({
        type: 'BUY',
        code: String(c.cycle_code || c.code),
        triggered_at: c.buy_triggered_at || c.triggered_at || generatedAt,
        label: c.label || `${c.cycle_code}h`
      }));

    // 只有存在多方信号时，空方信号才出现
    const sellItems = buyItems.length > 0 
      ? rawSellNodes
          .filter(n => n && (n.sell_state === 'SELL' || n.sell_state === 'OBSERVING' || n.state === 'SELL'))
          .map(n => ({
            type: 'SELL',
            code: String(n.code || n.cycle_code),
            triggered_at: n.triggered_at || generatedAt,
            label: n.label || `${n.code}m`
          }))
      : [];

    rawTimeline = [...buyItems, ...sellItems];
  }

  // 按接收时间先后升序排列
  const timeline = rawTimeline
    .filter(item => item && (item.type === 'BUY' || item.type === 'SELL'))
    .map(item => ({
      type: item.type, // 'BUY' (多) 或 'SELL' (空)
      code: String(item.code || item.cycle_code),
      triggered_at: item.triggered_at || item.buy_triggered_at || generatedAt,
      label: item.label || `${item.code}`
    }))
    .sort((a, b) => new Date(a.triggered_at).getTime() - new Date(b.triggered_at).getTime());

  const buyCount = timeline.filter(t => t.type === 'BUY').length;
  const sellCount = timeline.filter(t => t.type === 'SELL').length;
  const lastItem = timeline.length > 0 ? timeline[timeline.length - 1] : null;

  const payload = {
    schema_version: 'a-rolling-energy-v4',
    mode: 'live',
    generated_at: generatedAt,
    data_as_of: upstream.data_as_of || generatedAt,
    freshness: 'fresh',
    stale_after_seconds: staleAfterSeconds,
    notice: '每个时间段独立一格，多空上下交替对齐，按实际接收信号时间排序。',
    delivery: { state: 'live', reason: null },
    instrument: upstream.instrument || { instrument_name: '上海电力', exchange: 'SSE', symbol: '600021' },
    transmission: {
      state: timeline.length > 0 ? 'transmitting' : 'observing',
      basis: 'chronological_sequence',
      current_cycle_code: lastItem ? lastItem.code : null,
      started_at: timeline.length > 0 ? timeline[0].triggered_at : null,
      latest_triggered_at: lastItem ? lastItem.triggered_at : null,
      lit_count: timeline.length,
      buy_count: buyCount,
      sell_count: sellCount
    },
    timeline,
    cycles: timeline.filter(t => t.type === 'BUY').map(t => ({ cycle_code: t.code, buy_state: 'BUY', buy_triggered_at: t.triggered_at, label: t.label })),
    sell_chain: {
      nodes: timeline.filter(t => t.type === 'SELL').map(t => ({ code: t.code, sell_state: 'SELL', triggered_at: t.triggered_at, label: t.label }))
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
