import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('functions/api/public/v1/quote.js', new URL('../', import.meta.url)).pathname).href;
const { onRequestGet } = await import(moduleUrl);

test('quote API parses Tencent GBK stock payload correctly', async () => {
  const mockGbk = `v_sh600021="1~上海电力~600021~14.74~15.34~15.10~296747~113573~183104~14.74~243~14.73~718~14.72~892~14.71~1795~14.70~6706~14.75~234~14.76~233~14.77~211~14.78~117~14.79~324~~20260724100638~-0.60~-3.91~15.11~14.70~14.74/296747/440833465~296747~44083~1.05~16.60~~15.11~14.70~2.67~415.94~415.94~2.10~16.87~13.81~1.62~9235~14.86~18.34~15.03~~~1.56~44083.3465~0.0000~0~   A~GP-A~-24.99~-1.14~2.51~7.26~2.35~31.41~8.89~5.29~-1.99~-14.55~2821875805~2821875805~80.49~-45.47~2821875805~~~63.05~0.00~~CNY~0~___D__F__N~14.65~3102~";`;
  const encoder = new TextEncoder();

  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(encoder.encode(mockGbk), { status: 200 });

  try {
    const req = new Request('https://etf.peekabo.cc/api/public/v1/quote?symbol=600021&exchange=SSE');
    const res = await onRequestGet({ request: req });
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.symbol, '600021');
    assert.equal(data.price, 14.74);
    assert.equal(data.change_percent, -3.91);
    assert.equal(data.high, 15.11);
    assert.equal(data.low, 14.70);
    assert.equal(data.status, 'ok');
  } finally {
    globalThis.fetch = previous;
  }
});
