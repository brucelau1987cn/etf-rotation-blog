import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../public/js/etf-live-poll.js', import.meta.url), 'utf8');

function createHarness(initialIso) {
  let now = new Date(initialIso).getTime();
  const timers = [];
  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(value) { super(value === undefined ? now : value); }
    static now() { return now; }
  }
  const calendar = {
    session: {
      trade_date: '2026-08-17',
      is_open: 1,
      open_at: '2026-08-17T01:00:00Z',
      close_at: '2026-08-17T08:10:00Z',
    },
    next_open_session: { open_at: '2026-08-18T01:00:00Z' },
  };
  const context = {
    Date: FakeDate,
    Intl,
    Response,
    URL,
    console: { warn() {} },
    document: {
      hidden: false,
      addEventListener() {},
      removeEventListener() {},
    },
    fetch: async () => Response.json(calendar),
    setInterval(callback) { timers.push(callback); return timers.length; },
    clearInterval() {},
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return {
    poll: context.EtfLivePoll,
    advance(ms) { now += ms; },
    async pulse() {
      timers.forEach((timer) => timer());
      await new Promise((resolve) => setImmediate(resolve));
    },
    async settle() { await new Promise((resolve) => setImmediate(resolve)); },
  };
}

test('Hong Kong market poll runs immediately and every 60 seconds while open', async () => {
  const harness = createHarness('2026-08-17T02:00:00Z'); // 10:00 HKT
  let calls = 0;
  harness.poll.startMarketPoll({ market: 'HK', intervalMs: 60_000, tick: async () => { calls += 1; } });
  await harness.settle();
  assert.equal(calls, 1);
  harness.advance(59_000);
  await harness.pulse();
  assert.equal(calls, 1);
  harness.advance(1_000);
  await harness.pulse();
  assert.equal(calls, 2);
});

test('Hong Kong market poll makes no analysis request after close', async () => {
  const harness = createHarness('2026-08-17T15:30:00Z'); // 23:30 HKT
  let calls = 0;
  harness.poll.startMarketPoll({ market: 'HK', intervalMs: 60_000, tick: async () => { calls += 1; } });
  await harness.settle();
  harness.advance(10 * 60_000);
  await harness.pulse();
  assert.equal(calls, 0);
});
