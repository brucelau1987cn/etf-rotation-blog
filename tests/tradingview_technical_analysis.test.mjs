import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const apiUrl = pathToFileURL(new URL('functions/api/public/v1/technical-analysis.js', new URL('../', import.meta.url)).pathname).href;
const { classifyRecommendation, handleTechnicalAnalysis, technicalAnalysisCacheSize } = await import(apiUrl);

test('classifies TradingView recommendation scores at canonical thresholds', () => {
  assert.equal(classifyRecommendation(-0.75), 'STRONG_SELL');
  assert.equal(classifyRecommendation(-0.3), 'SELL');
  assert.equal(classifyRecommendation(0), 'NEUTRAL');
  assert.equal(classifyRecommendation(0.3), 'BUY');
  assert.equal(classifyRecommendation(0.75), 'STRONG_BUY');
  assert.equal(classifyRecommendation(null), 'UNAVAILABLE');
});

test('queries Hong Kong 4h/day/week analysis in one TradingView scan', async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    return Response.json({
      totalCount: 2,
      data: [
        { s: 'HKEX:1378', d: [-0.4242, -0.2666, 0.0314] },
        { s: 'HKEX:6809', d: [0.3575, 0.3216, null] },
      ],
    });
  };
  const request = new Request('https://etf.peekabo.cc/api/public/v1/technical-analysis?s=01378,06809');
  const response = await handleTechnicalAnalysis(request, fetchImpl);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://scanner.tradingview.com/hongkong/scan');
  assert.deepEqual(calls[0].body.symbols.tickers, ['HKEX:1378', 'HKEX:6809']);
  assert.deepEqual(calls[0].body.columns, ['Recommend.All|240', 'Recommend.All', 'Recommend.All|1W']);
  assert.deepEqual(body.results['01378'].map((x) => [x.period, x.recommendation]), [
    ['4h', 'SELL'], ['1d', 'SELL'], ['1W', 'NEUTRAL'],
  ]);
  assert.equal(body.results['06809'][2].recommendation, 'UNAVAILABLE');
});

test('bounds and canonicalizes the in-isolate response cache', async () => {
  const fetchImpl = async (_url, options) => {
    const ticker = JSON.parse(options.body).symbols.tickers[0];
    return Response.json({ totalCount: 1, data: [{ s: ticker, d: [0, 0, 0] }] });
  };
  for (let code = 1000; code < 1070; code += 1) {
    const response = await handleTechnicalAnalysis(new Request(`https://x/api/public/v1/technical-analysis?s=${code}`), fetchImpl);
    assert.equal(response.status, 200);
  }
  assert.ok(technicalAnalysisCacheSize() <= 64);
});

test('rejects invalid symbols and fails closed on upstream errors', async () => {
  const invalid = await handleTechnicalAnalysis(new Request('https://x/api/public/v1/technical-analysis?s=ABC,00000'), async () => {
    throw new Error('must not fetch');
  });
  assert.equal(invalid.status, 400);

  const failed = await handleTechnicalAnalysis(new Request('https://x/api/public/v1/technical-analysis?s=01378'), async () => new Response('blocked', { status: 403 }));
  assert.equal(failed.status, 503);
  assert.equal((await failed.json()).error, 'upstream_unavailable');
});
