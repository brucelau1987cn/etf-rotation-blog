/**
 * POST /api/v1/tradingview
 * 接收来自 TradingView 的多空能量传导预警信号 (Webhook)
 */

export async function onRequestPost({ request, env }) {
  try {
    const contentType = request.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      return new Response(JSON.stringify({ error: 'content-type must be application/json' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      });
    }

    const payload = await request.json();

    // 1. 验证 Token (优先从环境变量 TRADINGVIEW_WEBHOOK_TOKEN 取)
    const expectedToken = env.TRADINGVIEW_WEBHOOK_TOKEN || 'default_secret_token';
    if (!payload.webhook_token || payload.webhook_token !== expectedToken) {
      return new Response(JSON.stringify({ error: 'invalid webhook token' }), {
        status: 401,
        headers: { 'content-type': 'application/json' },
      });
    }

    // 2. 解析核心信号数据
    const {
      instrument_name = '上海电力',
      symbol = '600021',
      cycle_code,
      signal, // 'BUY' 或 'SELL'
      trigger_time_utc = new Date().toISOString(),
      event_id,
    } = payload;

    if (!cycle_code || !['BUY', 'SELL'].includes(signal)) {
      return new Response(JSON.stringify({ error: 'missing cycle_code or invalid signal type' }), {
        status: 422,
        headers: { 'content-type': 'application/json' },
      });
    }

    // 3. 如果绑定了 Cloudflare KV (如 env.ROLLING_KV)，可以将事件存入持久化存储
    if (env.ROLLING_KV) {
      const storageKey = `signal:${symbol}:${cycle_code}:${signal}`;
      await env.ROLLING_KV.put(storageKey, JSON.stringify({
        ...payload,
        received_at: new Date().toISOString(),
      }));
    }

    // 4. 返回 200 成功响应
    return new Response(
      JSON.stringify({
        success: true,
        message: `Signal ${signal} for cycle ${cycle_code} accepted`,
        event_id: event_id || `evt_${Date.now()}`,
        received_at: new Date().toISOString(),
      }),
      {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }
    );
  } catch (err) {
    return new Response(JSON.stringify({ error: 'failed to process webhook', details: err.message }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    });
  }
}
