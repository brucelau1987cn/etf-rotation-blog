import assert from 'node:assert/strict';
import test from 'node:test';
import { buildUsTradeLinks, usMarketDate, usSignalAnchor } from '../src/lib/us-paper-trade-links.mjs';

const records = [
  { date: '2026-07-28', signals: { harvest: [{ symbol: 'AAA', trigger_level: 11 }] } },
  { date: '2026-07-27', signals: { plant: [{ symbol: 'AAA', trigger_level: 10 }] } },
];
const events = [
  { id: 'buy', timestamp: '2026-07-28T13:35:00Z', symbol: 'AAA', side: 'buy', quantity: 10, price: 10, cost: 1, reason: 'plant' },
  { id: 'sell', timestamp: '2026-07-28T18:55:00Z', symbol: 'AAA', side: 'sell', quantity: 10, price: 11, cost: 1, reason: 'target', signal_date: '2026-07-27', signal_kind: 'plant' },
];

test('US timestamps use New York market dates', () => {
  assert.equal(usMarketDate('2026-07-28T01:00:00Z'), '2026-07-27');
});

test('buy and sell events link to their exact compass signals', () => {
  const links = buildUsTradeLinks(events, records);
  assert.equal(links[0].signalAnchor, usSignalAnchor('2026-07-27', 'AAA', 'plant'));
  assert.equal(links[1].signalAnchor, usSignalAnchor('2026-07-28', 'AAA', 'harvest'));
  assert.equal(links[0].gross, 100);
  assert.equal(links[1].realizedPnl, 8);
});

test('risk stop falls back to the originating buy signal when no exit signal exists', () => {
  const stop = { ...events[1], id: 'stop', reason: 'stop', price: 9 };
  const links = buildUsTradeLinks([events[0], stop], records.filter((item) => item.date === '2026-07-27'));
  assert.equal(links[1].signalAnchor, usSignalAnchor('2026-07-27', 'AAA', 'plant'));
  assert.equal(links[1].reasonLabel, '触及防守线卖出');
});
