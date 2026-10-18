import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(
  new URL('functions/api/public/v1/polymarket-prob.js', new URL('../', import.meta.url)).pathname,
).href;
const mod = await import(moduleUrl);
const handler = mod.onRequestGet || mod.default;

function mockEvent({ title, endDate, slug, yes }) {
  return [{
    slug,
    title,
    endDate,
    markets: [{
      id: '1',
      question: 'Will annual inflation be 3.4% in October?',
      outcomePrices: [String(yes), String(1 - yes)],
      endDate,
    }],
  }];
}

async function callWith(q, fetchImpl) {
  mod.__resetCaches?.();
  const previous = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try {
    const req = new Request(`https://etf.peekabo.cc/api/public/v1/polymarket-prob?q=${q}`);
    const res = await handler({ request: req });
    return { status: res.status, body: await res.json() };
  } finally {
    globalThis.fetch = previous;
  }
}

test('polymarket-prob 用当月活跃事件，不返回已结算的历史事件', async () => {
  const seen = [];
  const { status, body } = await callWith('cpi-yoy', async (url) => {
    seen.push(String(url));
    // 端点必须按 tag 发现当前活跃事件，而不是硬编码历史 slug
    if (String(url).includes('closed=false')) {
      return Response.json(mockEvent({
        title: 'October Inflation US - Annual',
        endDate: '2026-11-11T04:59:00Z',
        slug: 'october-inflation-us-annual',
        yes: 0.47,
      }));
    }
    return Response.json(mockEvent({
      title: 'July Inflation US - Annual',
      endDate: '2026-08-12T03:59:00Z',
      slug: 'july-inflation-us-annual-20260714150613267',
      yes: 1,
    }));
  });

  assert.equal(status, 200);
  assert.equal(body.ok, true);
  assert.match(body.event, /October/);
  assert.equal(body.top.yes_prob, 47);
  assert.ok(
    seen.some((u) => u.includes('closed=false')),
    'must discover the active event instead of trusting a hardcoded slug',
  );
});

test('polymarket-prob 在事件已过期时返回 ok:false，不把定格概率当成实时值', async () => {
  const { status, body } = await callWith('cpi-mom', async () => Response.json(mockEvent({
    title: 'July Inflation US - Monthly',
    endDate: '2020-01-01T00:00:00Z', // 早已结束
    slug: 'july-inflation-us-monthly-20260714151042665',
    yes: 1,
  })));

  assert.equal(body.ok, false);
  assert.match(String(body.error), /expired|stale|no active/i);
  assert.ok(status >= 400);
});

test('polymarket-prob 未知 q 仍返回 400', async () => {
  const { status, body } = await callWith('nope', async () => Response.json([]));
  assert.equal(status, 400);
  assert.equal(body.ok, false);
});
