import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const APP = new URL('../public/js/a-rolling-app.js', import.meta.url);
const MATRIX = new URL('../src/components/ARollingEnergyMatrix.astro', import.meta.url);
const appSource = readFileSync(APP, 'utf8');
const matrixSource = readFileSync(MATRIX, 'utf8');

const FORMAL_BUY_ORDER = ['2h', '2.5h', '3h', '3.5h', '4h', '4.5h', '5h', '5.5h', '6h', '6.5h', '7h', '7.5h', '8h'];
const FORMAL_SELL_ORDER = ['10m', '15m', '30m', '60m', '90m', '120m', '150m', '180m', '210m', '240m'];
const MAX_FORMAL_PER_SIDE = 4;
const buyOrderIndex = (code) => {
  const i = FORMAL_BUY_ORDER.indexOf(code);
  return i === -1 ? 999 : i;
};
const sellOrderIndex = (code) => {
  const i = FORMAL_SELL_ORDER.indexOf(code);
  return i === -1 ? 999 : i;
};
const signalTimeMs = (item) => {
  const ms = new Date(item?.triggered_at || item?.received_at || 0).getTime();
  return Number.isFinite(ms) ? ms : 0;
};
const takeLatestFormal = (items, orderIndex, limit = MAX_FORMAL_PER_SIDE) => {
  const sorted = items.slice().sort((a, b) => {
    const ta = signalTimeMs(a);
    const tb = signalTimeMs(b);
    if (ta !== tb) return ta - tb;
    const oa = orderIndex(a.code);
    const ob = orderIndex(b.code);
    if (oa !== ob) return oa - ob;
    return String(a.code || '').localeCompare(String(b.code || ''));
  });
  return sorted.slice(Math.max(0, sorted.length - limit));
};
const buildChronologicalColumns = (buys, sells) => {
  const columns = [
    ...buys.map((item) => ({ kind: 'BUY', item })),
    ...sells.map((item) => ({ kind: 'SELL', item })),
  ];
  return columns.sort((a, b) => {
    const ta = signalTimeMs(a.item);
    const tb = signalTimeMs(b.item);
    if (ta !== tb) return ta - tb;
    if (a.kind !== b.kind) return a.kind === 'SELL' ? -1 : 1;
    const orderIndex = a.kind === 'BUY' ? buyOrderIndex : sellOrderIndex;
    const oa = orderIndex(a.item.code);
    const ob = orderIndex(b.item.code);
    if (oa !== ob) return oa - ob;
    return String(a.item.code || '').localeCompare(String(b.item.code || ''));
  });
};

test('source encodes chronological shared-column ordering', () => {
  assert.match(appSource, /buildChronologicalColumns/);
  assert.match(appSource, /later BUY columns sit after earlier SELL columns/);
  assert.match(appSource, /Keep the latest N formal windows by trigger time/);
  assert.doesNotMatch(appSource, /Sell rail is left-padded by buy count so formal sells sit after buys/);
  assert.match(matrixSource, /buildChronologicalColumns/);
  assert.match(matrixSource, /列按触发时间从左到右排列/);
});

test('SI=F style timeline: sells first by time, later buys after them', () => {
  const timeline = [
    { type: 'SELL', code: '15m', triggered_at: '2026-07-31T07:15:00Z', price: 58.1266 },
    { type: 'SELL', code: '30m', triggered_at: '2026-07-31T14:00:00Z', price: 57.189 },
    { type: 'SELL', code: '60m', triggered_at: '2026-08-03T14:00:01Z', price: 57.1081 },
    { type: 'BUY', code: '1h45m', triggered_at: '2026-08-04T13:45:00Z', price: 59.7117 },
    { type: 'BUY', code: '4.5h', triggered_at: '2026-08-04T16:00:00Z', price: 59.7122 },
    { type: 'BUY', code: '4h', triggered_at: '2026-08-05T02:00:00Z', price: 59.904 },
  ];
  const buys = takeLatestFormal(timeline.filter((x) => x.type === 'BUY'), buyOrderIndex);
  const sells = takeLatestFormal(timeline.filter((x) => x.type === 'SELL'), sellOrderIndex);
  const columns = buildChronologicalColumns(buys, sells);

  assert.deepEqual(
    columns.map((c) => `${c.kind}:${c.item.code}`),
    ['SELL:15m', 'SELL:30m', 'SELL:60m', 'BUY:1h45m', 'BUY:4.5h', 'BUY:4h'],
  );

  // Old cycle-rank sort would put 4h before 4.5h / 1h45m; time sort must not.
  const buyCodes = buys.map((b) => b.code);
  assert.deepEqual(buyCodes, ['1h45m', '4.5h', '4h']);
});

test('new buy after a later sell sits to the right of that sell', () => {
  const buys = takeLatestFormal([
    { type: 'BUY', code: '2h', triggered_at: '2026-08-01T01:00:00Z' },
    { type: 'BUY', code: '3h', triggered_at: '2026-08-05T12:00:00Z' },
  ], buyOrderIndex);
  const sells = takeLatestFormal([
    { type: 'SELL', code: '15m', triggered_at: '2026-08-02T01:00:00Z' },
    { type: 'SELL', code: '30m', triggered_at: '2026-08-04T01:00:00Z' },
  ], sellOrderIndex);
  const columns = buildChronologicalColumns(buys, sells);
  assert.deepEqual(
    columns.map((c) => `${c.kind}:${c.item.code}`),
    ['BUY:2h', 'SELL:15m', 'SELL:30m', 'BUY:3h'],
  );
});

// 多方重置空方历史（原则 多方→空方→多方→空方）：
// 空方只保留最近一次正式多方之后的窗口；更早的空方（观察 5m 或正式）不再显示。
const roundSellsFor = (timeline) => {
  const latestBuyTime = timeline
    .filter((x) => x.type === 'BUY' && !(x.code === '1.75h' || x.code === '105m'))
    .reduce((max, item) => Math.max(max, signalTimeMs(item)), 0);
  return latestBuyTime > 0
    ? timeline.filter((item) => item.type === 'SELL' && signalTimeMs(item) >= latestBuyTime)
    : timeline.filter((item) => item.type === 'SELL');
};

test('sell history is reset by the latest formal buy (multi-round alternation)', () => {
  const timeline = [
    // 第 1 轮：空方先出
    { type: 'SELL', code: '5m', triggered_at: '2026-08-01T01:00:00Z' },
    { type: 'SELL', code: '10m', triggered_at: '2026-08-01T01:05:00Z' },
    { type: 'SELL', code: '15m', triggered_at: '2026-08-01T02:00:00Z' },
    // 第 1 轮多方：重置空方历史
    { type: 'BUY', code: '2h', triggered_at: '2026-08-01T04:00:00Z' },
    // 第 2 轮空方
    { type: 'SELL', code: '5m', triggered_at: '2026-08-02T01:00:00Z' },
    { type: 'SELL', code: '15m', triggered_at: '2026-08-02T02:00:00Z' },
    // 第 2 轮多方：再次重置
    { type: 'BUY', code: '3h', triggered_at: '2026-08-02T06:00:00Z' },
    // 第 3 轮空方
    { type: 'SELL', code: '30m', triggered_at: '2026-08-03T01:00:00Z' },
  ];
  const roundSells = roundSellsFor(timeline);
  // 只剩第 3 轮空方：更早的 5m/10m/15m 被第 2 轮多方覆盖，第 2 轮空方被第 3 轮多方覆盖
  assert.deepEqual(
    roundSells.map((s) => `${s.code}@${s.triggered_at.slice(0, 10)}`),
    ['30m@2026-08-03'],
  );
  // 观察窗也受同样规则约束
  assert.equal(roundSells.some((s) => s.code === '5m'), false);
});

test('no formal buy yet: all sells stay visible (first round)', () => {
  const timeline = [
    { type: 'SELL', code: '5m', triggered_at: '2026-08-01T01:00:00Z' },
    { type: 'SELL', code: '15m', triggered_at: '2026-08-01T02:00:00Z' },
  ];
  const roundSells = roundSellsFor(timeline);
  assert.deepEqual(roundSells.map((s) => s.code), ['5m', '15m']);
});

test('source encodes the multi-round sell reset in both app and matrix', () => {
  assert.match(appSource, /多方重置空方历史/);
  assert.match(matrixSource, /多方重置空方历史/);
  assert.match(appSource, /roundSells/);
  assert.match(matrixSource, /roundSells/);
});
