import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../functions/api/public/v1/jin10-mcp-calendar.js', import.meta.url).pathname).href;
const { handleMcpCalendar, parseSseJson } = await import(moduleUrl);

const sse = (payload) => `event: message\ndata: ${JSON.stringify(payload)}\n\n`;

const mcpSessionResponse = sse({
  jsonrpc: '2.0',
  id: 1,
  result: {
    protocolVersion: '2025-11-25',
    capabilities: { tools: { listChanged: true } },
    serverInfo: { name: 'mcp-server', version: '1.0.0' },
  },
});

const mcpCalendarResponse = sse({
  jsonrpc: '2.0',
  id: 2,
  result: {
    content: [{ type: 'text', text: JSON.stringify({
      status: 200,
      message: '',
      data: [
        { pub_time: '2026-08-01 01:00', star: 3, title: '美国至7月31日当周石油钻井总数(口)', previous: '450', consensus: null, actual: '451', revised: null, affect_txt: '利空' },
        { pub_time: '2026-07-31 20:30', star: 3, title: '美国6月核心PCE物价指数年率', previous: '2.60', consensus: '2.70', actual: '2.8', revised: null, affect_txt: '利多' },
        { pub_time: '2026-07-28 09:30', star: 2, title: '中国6月规模以上工业企业利润年率', previous: '21.10', consensus: null, actual: '15.1', revised: null, affect_txt: '' },
      ],
    }) }],
    structuredContent: {
      data: [
        { pub_time: '2026-08-01 01:00', star: 3, title: '美国至7月31日当周石油钻井总数(口)', previous: '450', consensus: null, actual: '451', revised: null, affect_txt: '利空' },
        { pub_time: '2026-07-31 20:30', star: 3, title: '美国6月核心PCE物价指数年率', previous: '2.60', consensus: '2.70', actual: '2.8', revised: null, affect_txt: '利多' },
        { pub_time: '2026-07-28 09:30', star: 2, title: '中国6月规模以上工业企业利润年率', previous: '21.10', consensus: null, actual: '15.1', revised: null, affect_txt: '' },
      ],
    },
  },
});

const makeFetch = (responses) => {
  const calls = [];
  return {
    calls,
    fn: async (url, options) => {
      calls.push({ url: String(url), options, body: options?.body ? JSON.parse(options.body) : null });
      const next = responses.shift();
      if (!next) return new Response('', { status: 502 });
      return new Response(next.body, { status: 200, headers: { 'content-type': 'text/event-stream', 'mcp-session-id': next.sessionId || '' } });
    },
  };
};

test('parseSseJson extracts the data payload from SSE framing', () => {
  const payload = parseSseJson('event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{"ok":true}}\n\n');
  assert.equal(payload.result.ok, true);
});

test('handleMcpCalendar performs initialize then list_calendar and returns normalized items', async () => {
  const fake = makeFetch([
    { body: mcpSessionResponse, sessionId: 'sess-abc-123' },
    { body: mcpCalendarResponse, sessionId: 'sess-abc-123' },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token', fetchImpl: fake.fn });
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.status, 'ok');
  assert.equal(payload.source, 'jin10-mcp');
  assert.equal(payload.count, 3);
  assert.deepEqual(payload.counts, { bullish: 1, bearish: 1, neutral: 1 });

  // initialize first, then tools/call with session id
  assert.equal(fake.calls[0].body.method, 'initialize');
  assert.equal(fake.calls[1].body.method, 'tools/call');
  assert.equal(fake.calls[1].body.params.name, 'list_calendar');
  assert.equal(fake.calls[1].options.headers['mcp-session-id'], 'sess-abc-123');
  assert.equal(fake.calls[1].options.headers['authorization'], 'Bearer sk-test-token');

  const rig = payload.items.find((item) => item.title.includes('石油钻井'));
  assert.equal(rig.affect_txt, '利空');
  assert.equal(rig.impact, '利空');
  const pce = payload.items.find((item) => item.title.includes('PCE'));
  assert.equal(pce.affect_txt, '利多');
  assert.equal(pce.impact, '利多');
});

test('handleMcpCalendar returns 401 when token is missing', async () => {
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar');
  const response = await handleMcpCalendar(request, {});
  assert.equal(response.status, 401);
  const payload = await response.json();
  assert.equal(payload.error, 'unauthorized');
});

test('handleMcpCalendar returns 502 when initialize fails', async () => {
  const fake = makeFetch([{ body: 'not sse at all', sessionId: '' }]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token', fetchImpl: fake.fn });
  const payload = await response.json();
  assert.equal(response.status, 502);
  assert.match(payload.detail, /mcp response/);
});

test('handleMcpCalendar returns 502 when MCP reports a business error', async () => {
  const errorSse = sse({ jsonrpc: '2.0', id: 2, error: { code: -32000, message: '今日该工具调用次数已达上限，请明日再试' } });
  const fake = makeFetch([
    { body: mcpSessionResponse, sessionId: 'sess-abc-123' },
    { body: errorSse, sessionId: 'sess-abc-123' },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token', fetchImpl: fake.fn });
  const payload = await response.json();
  assert.equal(response.status, 502);
  assert.match(payload.error, /调用次数已达上限/);
});
