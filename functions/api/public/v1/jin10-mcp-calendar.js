/**
 * MCP client for Jin10 calendar data.
 *
 * Uses direct JSON-RPC over HTTP/SSE (no MCP SDK needed).
 * Flow: initialize → tools/call list_calendar → parse SSE → normalize.
 *
 * Requires env.JIN10_MCP_TOKEN (single or comma-separated Bearer tokens).
 * Multiple tokens are rotated round-robin by minute to multiply daily quota
 * (1500 calls/day/tool/user per token).
 */

const MCP_URL = 'https://mcp.jin10.com/mcp';
const MCP_PROTOCOL = '2025-11-25';

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=60, s-maxage=300, stale-while-revalidate=600',
    'x-content-type-options': 'nosniff',
  },
});

/**
 * Parse a single SSE data line from the response body.
 * The MCP server returns: event: message\ndata: <JSON>\n\n
 */
export const parseSseJson = (body) => {
  for (const line of String(body).split('\n')) {
    if (line.startsWith('data: ')) {
      return JSON.parse(line.slice(6));
    }
  }
  throw new Error('no SSE data line found');
};

/**
 * POST to the MCP server, parse SSE response, return parsed JSON + session id.
 */
const mcpPost = async (url, token, body, sessionId = null, fetchImpl = fetch) => {
  const headers = {
    'content-type': 'application/json',
    accept: 'application/json, text/event-stream',
    authorization: `Bearer ${token}`,
    'user-agent': 'HermesETF/1.0',
  };
  if (sessionId) headers['mcp-session-id'] = sessionId;

  const response = await fetchImpl(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  const status = response.status;
  const text = await response.text();
  const newSessionId = response.headers.get('mcp-session-id') || sessionId;

  if (!text.includes('data: ')) {
    throw new Error(`mcp response status=${status} body=${text.slice(0, 300)}`);
  }

  const payload = parseSseJson(text);
  return { payload, sessionId: newSessionId };
};

/**
 * Handle GET /api/public/v1/jin10-mcp-calendar
 *
 * Returns the current week's calendar data from the Jin10 MCP server,
 * with affect_txt (利空/利多/影响较小) for every item.
 */
export async function handleMcpCalendar(request, env = {}) {
  const rawTokens = String(env.JIN10_MCP_TOKEN || '').trim();
  if (!rawTokens) return json({ error: 'unauthorized', source: 'jin10-mcp' }, 401);
  const tokens = rawTokens.split(',').map((t) => t.trim()).filter(Boolean);
  if (tokens.length === 0) return json({ error: 'unauthorized', source: 'jin10-mcp' }, 401);
  // Round-robin by minute to distribute across tokens
  const token = tokens[Math.floor(Date.now() / 60000) % tokens.length];
  if (!token) return json({ error: 'unauthorized', source: 'jin10-mcp' }, 401);
  const fetchImpl = env.fetchImpl || fetch;

  try {
    // 1. initialize
    const { payload: initPayload, sessionId } = await mcpPost(MCP_URL, token, {
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: MCP_PROTOCOL,
        capabilities: {},
        clientInfo: { name: 'hermes-agent', version: '1.0.0' },
      },
    }, null, fetchImpl);

    if (!sessionId || initPayload?.error) {
      return json({ error: 'mcp initialization failed', detail: initPayload?.error?.message || null }, 502);
    }

    // 2. tools/call list_calendar
    const { payload: calPayload } = await mcpPost(MCP_URL, token, {
      jsonrpc: '2.0',
      id: 2,
      method: 'tools/call',
      params: { name: 'list_calendar', arguments: {} },
    }, sessionId, fetchImpl);

    if (calPayload?.error) {
      const msg = String(calPayload.error.message || 'mcp error');
      return json({ error: msg, source: 'jin10-mcp' }, 502);
    }

    // 3. Extract data: prefer structuredContent, fall back to content[0].text
    const result = calPayload?.result;
    let rawData = null;

    if (result?.structuredContent?.data) {
      rawData = result.structuredContent.data;
    } else if (result?.content?.[0]?.text) {
      const inner = JSON.parse(result.content[0].text);
      rawData = inner?.data || null;
    }

    if (!Array.isArray(rawData)) {
      return json({ error: 'invalid mcp calendar response', source: 'jin10-mcp' }, 502);
    }

    // 4. Normalize
    const items = rawData.map((item) => ({
      time: item.pub_time || null,
      star: Number.isFinite(Number(item.star)) ? Number(item.star) : null,
      title: String(item.title || '未命名事项'),
      previous: item.previous ?? null,
      consensus: item.consensus ?? null,
      actual: item.actual ?? null,
      revised: item.revised ?? null,
      affect_txt: String(item.affect_txt || '').trim() || null,
      impact: String(item.affect_txt || '').trim() || null,
    }));

    const counts = { bullish: 0, bearish: 0, neutral: 0 };
    for (const item of items) {
      if (item.affect_txt === '利多') counts.bullish += 1;
      else if (item.affect_txt === '利空') counts.bearish += 1;
      else counts.neutral += 1;
    }

    return json({
      status: 'ok',
      source: 'jin10-mcp',
      count: items.length,
      counts,
      items,
    });
  } catch (error) {
    return json({ error: 'mcp upstream unavailable', detail: error?.message || null, source: 'jin10-mcp' }, 502);
  }
}

export async function onRequestGet({ request, env = {} }) {
  return handleMcpCalendar(request, env);
}