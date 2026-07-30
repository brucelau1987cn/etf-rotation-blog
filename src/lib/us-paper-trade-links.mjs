const US_TIME_ZONE = 'America/New_York';

const parts = (timestamp, options) => Object.fromEntries(
  new Intl.DateTimeFormat('en-US', { timeZone: US_TIME_ZONE, ...options })
    .formatToParts(new Date(timestamp))
    .filter((item) => item.type !== 'literal')
    .map((item) => [item.type, item.value]),
);

export const usMarketDate = (timestamp) => {
  const value = parts(timestamp, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${value.year}-${value.month}-${value.day}`;
};

export const usMarketTime = (timestamp) => {
  const value = parts(timestamp, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  return `${value.hour}:${value.minute}:${value.second} ET`;
};

const safe = (value) => String(value || '').replace(/[^A-Za-z0-9_-]+/g, '-');
export const usSignalAnchor = (date, symbol, kind) => `us-signal-${safe(date)}-${safe(symbol)}-${safe(kind)}`;
export const paperTradeAnchor = (id) => `trade-${safe(id)}`;

const signalInRecord = (record, symbol, kinds) => {
  for (const kind of kinds) {
    const signal = (record?.signals?.[kind] || []).find((item) => item.symbol === symbol);
    if (signal) return { date: record.date, kind, signal };
  }
  return null;
};

const exactSignal = (records, event) => {
  if (!event.signal_date) return null;
  const record = records.find((item) => item.date === event.signal_date);
  return signalInRecord(record, event.symbol, [event.signal_kind || 'plant']);
};

const inferBuySignal = (records, event, executionDate) => {
  const exact = exactSignal(records, event);
  if (exact) return exact;
  return [...records]
    .filter((record) => record.date < executionDate)
    .sort((a, b) => b.date.localeCompare(a.date))
    .map((record) => signalInRecord(record, event.symbol, ['plant']))
    .find(Boolean) || null;
};

const inferSellSignal = (records, event, executionDate) => {
  const kinds = event.reason === 'target' || event.reason === 'harvest'
    ? ['harvest']
    : event.reason === 'stop' || event.reason === 'exit'
      ? ['exit']
      : ['harvest', 'exit'];
  const record = records.find((item) => item.date === executionDate);
  const actionSignal = signalInRecord(record, event.symbol, kinds);
  return actionSignal || exactSignal(records, event);
};

const reasonLabels = {
  plant: '正式伏击买入',
  target: '触及兑现位卖出',
  harvest: '兑现信号卖出',
  stop: '触及防守线卖出',
  exit: '破位撤退卖出',
};

export function buildUsTradeLinks(events = [], records = []) {
  const ordered = [...events].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  const openBuys = new Map();

  return ordered.map((event) => {
    const executionDate = usMarketDate(event.timestamp);
    const executionTime = usMarketTime(event.timestamp);
    let actionSignal = null;
    let entryTrade = openBuys.get(event.symbol) || null;

    if (event.side === 'buy') actionSignal = inferBuySignal(records, event, executionDate);
    else actionSignal = inferSellSignal(records, event, executionDate);

    const linkedSignal = actionSignal || entryTrade?.linkedSignal || null;
    const gross = Number(event.price || 0) * Number(event.quantity || 0);
    const realizedPnl = event.side === 'sell' && entryTrade
      ? (Number(event.price || 0) - Number(entryTrade.event.price || 0)) * Number(event.quantity || 0)
        - Number(entryTrade.event.cost || 0) - Number(event.cost || 0)
      : null;
    const row = {
      event,
      executionDate,
      executionTime,
      actionLabel: event.side === 'buy' ? '买入' : '卖出',
      reasonLabel: reasonLabels[event.reason] || event.reason || '规则成交',
      gross,
      realizedPnl,
      linkedSignal,
      entryTrade,
      tradeAnchor: paperTradeAnchor(event.id),
      signalAnchor: linkedSignal ? usSignalAnchor(linkedSignal.date, event.symbol, linkedSignal.kind) : null,
      signalHref: linkedSignal ? `/us-compass/history/#${usSignalAnchor(linkedSignal.date, event.symbol, linkedSignal.kind)}` : '/us-compass/history/',
    };

    if (event.side === 'buy') openBuys.set(event.symbol, row);
    else openBuys.delete(event.symbol);
    return row;
  });
}
