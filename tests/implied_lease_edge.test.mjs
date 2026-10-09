import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(
  new URL('functions/api/public/v1/implied-lease-rate.js', new URL('../', import.meta.url)).pathname,
).href;
const mod = await import(moduleUrl);

const day = 86400000;
const expiry = (iso) => new Date(`${iso}T00:00:00Z`);

// Cloudflare's Cache API is absent under node --test; without a stub the
// handler throws and falls into its error branch, hiding the real logic.
if (!globalThis.caches) {
  const store = new Map();
  globalThis.caches = {
    default: {
      async match() { return undefined; },
      async put() { /* no-op: never serve a cached payload across tests */ },
      __store: store,
    },
  };
}

// The edge function is a second, independent implementation of the lease math,
// and the page prefers it over the committed static payload. The 2026-10-09
// regression (thin front month GCV26, 13 lots → gold 1M at -1.202%) therefore
// has to be guarded here too, or the fix is bypassed in the browser.

function chartMeta(price, name, volume, marketTime) {
  return JSON.stringify({
    chart: {
      result: [{
        meta: {
          regularMarketPrice: price,
          shortName: name,
          fullExchangeName: 'COMEX',
          regularMarketVolume: volume,
          regularMarketTime: marketTime,
        },
      }],
    },
  });
}

function stubFetch(contracts) {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const urlStr = typeof url === 'string' ? url : url.url;
    if (urlStr.includes('treasury.gov')) {
      return new Response(
        'Date,"1 Mo","3 Mo","6 Mo","1 Yr"\n10/08/2026,4.14,4.23,4.30,4.44\n',
        { status: 200 },
      );
    }
    for (const [symbol, meta] of Object.entries(contracts)) {
      if (urlStr.includes(symbol)) {
        return new Response(chartMeta(meta.price, meta.name, meta.volume, meta.marketTime), { status: 200 });
      }
    }
    return new Response('not found', { status: 404 });
  };
  return () => { globalThis.fetch = previous; };
}

test('implied-lease-rate skips a thin front month as spot leg', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const restore = stubFetch({
    'GCV26.CMX': { price: 4176.6, name: 'Gold Oct 26', volume: 13, marketTime: nowSec },
    'GCZ26.CMX': { price: 4210.0, name: 'Gold Dec 26', volume: 55704, marketTime: nowSec },
    'GCG27.CMX': { price: 4243.7, name: 'Gold Feb 27', volume: 1709, marketTime: nowSec },
    'GCJ27.CMX': { price: 4280.3, name: 'Gold Apr 27', volume: 644, marketTime: nowSec },
  });
  try {
    const body = await mod.onRequestGet({ request: new Request('https://x/api') });
    const payload = await body.json();
    const gold = payload.data.gold;
    assert.ok(gold, 'gold block should be present');
    assert.equal(gold.front.symbol, 'GCZ26.CMX', 'spot leg should advance to the liquid month');
    assert.match(gold.spot_leg_reason, /advanced spot leg/);
    assert.equal(gold.spot_leg_rejected.symbol, 'GCV26.CMX');
    assert.ok(gold.rate_1m > 0, `gold 1M should be positive, got ${gold.rate_1m}`);
  } finally {
    restore();
  }
});

test('implied-lease-rate keeps a liquid front month and reports no degradation', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const restore = stubFetch({
    'SIZ26.CMX': { price: 60.645, name: 'Silver Dec 26', volume: 10376, marketTime: nowSec },
    'SIH27.CMX': { price: 61.385, name: 'Silver Mar 27', volume: 651, marketTime: nowSec },
  });
  try {
    const body = await mod.onRequestGet({ request: new Request('https://x/api') });
    const payload = await body.json();
    assert.equal(payload.data.silver.front.symbol, 'SIZ26.CMX');
    assert.equal(payload.data.silver.spot_leg_rejected, null);
    assert.deepEqual(payload.data.degraded_metals, ['gold'], 'missing gold curve is reported');
  } finally {
    restore();
  }
});

test('implied-lease-rate marks a metal degraded when no spot leg qualifies', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const restore = stubFetch({
    'GCV26.CMX': { price: 4176.6, name: 'Gold Oct 26', volume: 1, marketTime: nowSec },
    'GCZ26.CMX': { price: 4210.0, name: 'Gold Dec 26', volume: 2, marketTime: nowSec },
  });
  try {
    const body = await mod.onRequestGet({ request: new Request('https://x/api') });
    const payload = await body.json();
    assert.equal(payload.data.gold, null);
    assert.deepEqual(payload.data.degraded_metals, ['gold', 'silver']);
  } finally {
    restore();
  }
});
