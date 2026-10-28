import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(
  new URL('functions/api/public/v1/implied-lease-rate.js', new URL('../', import.meta.url)).pathname,
).href;
const mod = await import(moduleUrl);

const day = 86400000;
const expiry = (iso) => new Date(`${iso}T00:00:00Z`);

// Contract symbols are a fixed COMEX month set (the edge function requests
// `GC|SI` × `GJMQVZ|HKNUZ` × 26/27/28). Hardcoding "GCV26" made these tests a
// time bomb: once the 27th of that month passed, the guard filtered the
// contract out and the fixture stopped exercising the thin-front path at all.
const MONTH_NUM = { F: 1, G: 2, H: 3, J: 4, K: 5, M: 6, N: 7, Q: 8, U: 9, V: 10, X: 11, Z: 12 };
const MONTH_NAME = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function cnToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const map = Object.fromEntries(parts.filter((p) => p.type !== 'literal').map((p) => [p.type, p.value]));
  return new Date(Date.UTC(Number(map.year), Number(map.month) - 1, Number(map.day)));
}

/** The next `count` listed contracts, nearest expiry first, anchored to today. */
function upcomingContracts(prefix, codes, count) {
  const today = cnToday();
  const metal = prefix === 'GC' ? 'Gold' : 'Silver';
  const all = [];
  for (const y of [26, 27, 28]) {
    for (const c of codes) {
      const month = MONTH_NUM[c];
      const exp = new Date(Date.UTC(2000 + y, month - 1, 27));
      if (exp.getTime() - today.getTime() > 5 * day) {
        all.push({ symbol: `${prefix}${c}${y}.CMX`, name: `${metal} ${MONTH_NAME[month - 1]} ${y}`, expiry: exp });
      }
    }
  }
  all.sort((a, b) => a.expiry - b.expiry);
  return all.slice(0, count);
}

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
  const [thin, liquid, third, fourth] = upcomingContracts('GC', 'GJMQVZ', 4);
  const restore = stubFetch({
    [thin.symbol]: { price: 4176.6, name: thin.name, volume: 13, marketTime: nowSec },
    [liquid.symbol]: { price: 4210.0, name: liquid.name, volume: 55704, marketTime: nowSec },
    [third.symbol]: { price: 4243.7, name: third.name, volume: 1709, marketTime: nowSec },
    [fourth.symbol]: { price: 4280.3, name: fourth.name, volume: 644, marketTime: nowSec },
  });
  try {
    const body = await mod.onRequestGet({ request: new Request('https://x/api') });
    const payload = await body.json();
    const gold = payload.data.gold;
    assert.ok(gold, 'gold block should be present');
    assert.equal(gold.front.symbol, liquid.symbol, 'spot leg should advance to the liquid month');
    assert.match(gold.spot_leg_reason, /advanced spot leg/);
    assert.equal(gold.spot_leg_rejected.symbol, thin.symbol);
    assert.ok(gold.rate_1m > 0, `gold 1M should be positive, got ${gold.rate_1m}`);
  } finally {
    restore();
  }
});

test('implied-lease-rate keeps a liquid front month and reports no degradation', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const [front, next] = upcomingContracts('SI', 'HKNUZ', 2);
  const restore = stubFetch({
    [front.symbol]: { price: 60.645, name: front.name, volume: 10376, marketTime: nowSec },
    [next.symbol]: { price: 61.385, name: next.name, volume: 651, marketTime: nowSec },
  });
  try {
    const body = await mod.onRequestGet({ request: new Request('https://x/api') });
    const payload = await body.json();
    assert.equal(payload.data.silver.front.symbol, front.symbol);
    assert.equal(payload.data.silver.spot_leg_rejected, null);
    assert.deepEqual(payload.data.degraded_metals, ['gold'], 'missing gold curve is reported');
  } finally {
    restore();
  }
});

test('implied-lease-rate marks a metal degraded when no spot leg qualifies', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const [thin, thinNext] = upcomingContracts('GC', 'GJMQVZ', 2);
  const restore = stubFetch({
    [thin.symbol]: { price: 4176.6, name: thin.name, volume: 1, marketTime: nowSec },
    [thinNext.symbol]: { price: 4210.0, name: thinNext.name, volume: 2, marketTime: nowSec },
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
