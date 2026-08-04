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

test('handleMcpCalendar rotates multiple comma-separated tokens by minute', async () => {
  const fake = makeFetch([
    { body: mcpSessionResponse, sessionId: 'sess-multi-1' },
    { body: mcpCalendarResponse, sessionId: 'sess-multi-1' },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar');
  // Two tokens: token-a and token-b. Current minute parity decides which is used.
  const minuteBucket = Math.floor(Date.now() / 60000) % 2;
  const response = await handleMcpCalendar(request, {
    JIN10_MCP_TOKEN: 'sk-token-a, sk-token-b',
    fetchImpl: fake.fn,
  });
  assert.equal(response.status, 200);
  const expectedToken = minuteBucket === 0 ? 'sk-token-a' : 'sk-token-b';
  assert.equal(fake.calls[0].options.headers['authorization'], `Bearer ${expectedToken}`);
  assert.equal(fake.calls[1].options.headers['authorization'], `Bearer ${expectedToken}`);
});

test('handleMcpCalendar supports a safe search_news query for futures policy briefing', async () => {
  const searchResponse = sse({
    jsonrpc: '2.0', id: 2, result: {
      structuredContent: { data: [{ id: 'n1', title: '工信部发布多晶硅行业政策', summary: '推动绿色产能', url: 'https://example/news', pub_time: '2026-08-02 10:00' }] },
    },
  });
  const fake = makeFetch([
    { body: mcpSessionResponse, sessionId: 'sess-search' },
    { body: searchResponse, sessionId: 'sess-search' },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar?tool=search_news&keyword=%E5%A4%9A%E6%99%B6%E7%A1%85%E6%94%BF%E7%AD%96');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token', fetchImpl: fake.fn });
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(fake.calls[1].body.params.name, 'search_news');
  assert.deepEqual(fake.calls[1].body.params.arguments, { keyword: '多晶硅政策' });
  assert.equal(payload.items[0].title, '工信部发布多晶硅行业政策');
  assert.equal(payload.items[0].url, 'https://example/news');
});

test('handleMcpCalendar rejects unsupported tool names', async () => {
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar?tool=get_quote');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token' });
  assert.equal(response.status, 400);
});

test('handleMcpCalendar extracts search results wrapped in a data object', async () => {
  const searchResponse = sse({
    jsonrpc: '2.0', id: 2, result: {
      structuredContent: { data: { list: [{ id: 'n2', title: '新能源材料政策', url: 'https://example/n2' }] } },
    },
  });
  const fake = makeFetch([
    { body: mcpSessionResponse, sessionId: 'sess-wrapped' },
    { body: searchResponse, sessionId: 'sess-wrapped' },
  ]);
  const request = new Request('https://etf.peekabo.cc/api/public/v1/jin10-mcp-calendar?tool=search_news&keyword=%E6%96%B0%E8%83%BD%E6%BA%90');
  const response = await handleMcpCalendar(request, { JIN10_MCP_TOKEN: 'sk-test-token', fetchImpl: fake.fn });
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.items[0].title, '新能源材料政策');
});
