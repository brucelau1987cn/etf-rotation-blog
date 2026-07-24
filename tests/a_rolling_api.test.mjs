import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const libUrl = pathToFileURL(new URL('functions/_lib/a-rolling.js', new URL('../', import.meta.url)).pathname).href;
const { projectUpstream } = await import(libUrl);

test('projectUpstream formats buy & sell chains on-demand without placeholders', () => {
  const upstream = {
    data_as_of: '2026-07-24T12:00:00Z',
    cycles: [
      { cycle_code: '2h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:30:00Z' },
      { cycle_code: '2.5h', buy_state: 'BUY', buy_triggered_at: '2026-07-24T09:45:00Z' },
    ],
    sell_chain: {
      nodes: [
        { code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }
      ]
    }
  };

  const res = projectUpstream(upstream);
  assert.equal(res.schema_version, 'a-rolling-energy-v4');
  assert.equal(res.cycles.length, 2);
  assert.equal(res.sell_chain.nodes.length, 1);
});

test('sell chain is hidden if there are no buy signals', () => {
  const upstream = {
    cycles: [],
    sell_chain: {
      nodes: [
        { code: '10m', sell_state: 'SELL', triggered_at: '2026-07-24T10:00:00Z' }
      ]
    }
  };

  const res = projectUpstream(upstream);
  assert.equal(res.cycles.length, 0);
  assert.equal(res.sell_chain.nodes.length, 0);
});
