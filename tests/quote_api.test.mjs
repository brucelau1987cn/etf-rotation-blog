import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('functions/api/public/v1/quote.js', new URL('../', import.meta.url)).pathname).href;
const { onRequestGet } = await import(moduleUrl);

test('quote API supports both single and batch queries', async () => {
  const mockGbk = `v_sh600021="1~上海电力~600021~14.60~15.34~15.10~406482~155174~250958~14.60~5704~14.59~346~14.58~1476~14.57~761~14.56~1464~14.61~179~14.62~232~14.63~30~14.64~345~14.65~712~~20260724104135~-0.74~-4.82~15.11~14.60~14.60/406482/601828334~406482~60183~1.44~16.44~~15.11~14.60~3.32~411.99~411.99~2.08~16.87~13.81~1.14~8253~14.81~18.16~14.89~~~1.56~60182.8334~0.0000~0~   A~GP-A~-25.70~-2.08~2.53~7.26~2.35~31.41~8.89~4.29~-2.93~-15.36~2821875805~2821875805~73.37~-45.99~2821875805~~~61.50~-0.34~~CNY~0~___D__F__N~14.51~944~";
v_sh517520="1~黄金股ETF永赢~517520~1.808~1.886~1.791~3339573~1690342~1649061~1.807~7861~1.806~6754~1.805~1310~1.804~3272~1.803~4392~1.808~56~1.809~9427~1.810~2343~1.811~1344~1.812~14667~~20260724104137~-0.078~-4.14~1.841~1.791~1.808/3339573/607557936~3339573~60756~5.35~~~1.841~1.791~2.65~112.79~112.79~0.00~2.075~1.697~3.17~-4248~1.819~~~~~~60755.7936~0.0000~0~   A~ETF~-11.37~11.33~~~~3.391~1.415~5.36~13.93~-17.03~6238404000~6238404000~-8.26~-5.24~6238404000~0.27~1.8031~20.86~-0.33~1.8823~CNY~0~___D__F__N~1.800~18819~";`;
  
  const encoder = new TextEncoder();
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(encoder.encode(mockGbk), { status: 200 });

  try {
    // 1. 测试单标的兼容响应
    const reqSingle = new Request('https://etf.peekabo.cc/api/public/v1/quote?symbol=600021&exchange=SSE');
    const resSingle = await onRequestGet({ request: reqSingle });
    const dataSingle = await resSingle.json();
    assert.equal(dataSingle.symbol, '600021');
    assert.equal(dataSingle.price, 14.6);
    assert.equal(dataSingle.change_percent, -4.82);

    // 2. 测试多标的批量响应
    const reqBatch = new Request('https://etf.peekabo.cc/api/public/v1/quote?symbols=600021.SH,517520.SH');
    const resBatch = await onRequestGet({ request: reqBatch });
    const dataBatch = await resBatch.json();
    assert.equal(dataBatch.status, 'ok');
    assert.equal(dataBatch.count, 2);
    assert.equal(dataBatch.quotes['600021'].price, 14.6);
    assert.equal(dataBatch.quotes['517520'].price, 1.808);
  } finally {
    globalThis.fetch = previous;
  }
});
