import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(
  new URL('functions/api/public/v1/polymarket-prob.js', new URL('../', import.meta.url)).pathname,
).href;
const mod = await import(moduleUrl);
const handler = mod.onRequestGet || mod.default;

const active = [{
  slug: 'october-inflation-us-annual',
  title: 'October Inflation US - Annual',
  endDate: '2026-11-11T04:59:00Z',
  markets: [{
    id: '1',
    question: 'Will annual inflation be 3.7% in October?',
    outcomePrices: ['0.79', '0.21'],
    endDate: '2026-11-11T04:59:00Z',
  }],
}];

async function callWith(q, fetchImpl) {
  mod.__resetCaches?.();
  const previous = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try {
    const res = await handler({ request: new Request(`https://x/api?q=${q}`) });
    return { status: res.status, body: await res.json() };
  } finally {
    globalThis.fetch = previous;
  }
}

test('covered_month 从无年份标题 + 结算日推出数据月份', async () => {
  const { body } = await callWith('cpi-yoy', async () => Response.json(active));
  // "October ..." 结算在 2026-11，因此覆盖月份是 2026-10
  assert.equal(body.covered_month, '2026-10');
});

test('covered_month 从带年份的标题直接取年月', async () => {
  const core = [{
    slug: 'core-cpi-yoy-october-2026',
    title: 'Core CPI YoY - October 2026',
    endDate: '2026-11-11T04:59:00Z',
    markets: [{ id: '2', question: 'Will Core CPI YoY be 2.6% in October?', outcomePrices: ['0.675', '0.325'], endDate: '2026-11-11T04:59:00Z' }],
  }];
  const { body } = await callWith('core-cpi-yoy', async () => Response.json(core));
  assert.equal(body.covered_month, '2026-10');
});

test('跨年标题按结算日回推上一年', async () => {
  const dec = [{
    slug: 'december-inflation-us-annual',
    title: 'December Inflation US - Annual',
    endDate: '2027-01-13T04:59:00Z', // 12 月数据在次年 1 月发布
    markets: [{ id: '3', question: 'Will annual inflation be 3.1% in December?', outcomePrices: ['0.4', '0.6'], endDate: '2027-01-13T04:59:00Z' }],
  }];
  const { body } = await callWith('cpi-yoy', async () => Response.json(dec));
  assert.equal(body.covered_month, '2026-12');
});
