/**
 * POST /api/v1/tradingview
 * 接收 TradingView Webhook 多空信号并存入 Cloudflare KV (ROLLING_KV)
 */

export async function onRequestPost({ request, env }) {
  try {
    const expectedToken = String(env.TRADINGVIEW_WEBHOOK_TOKEN || '').trim();
    if (!expectedToken) {
      return new Response(JSON.stringify({ error: 'TRADINGVIEW_WEBHOOK_TOKEN missing on server' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      });
    }

    const contentType = request.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      return new Response(JSON.stringify({ error: 'content-type must be application/json' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      });
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return new Response(JSON.stringify({ error: 'invalid json payload' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      });
    }

    if (!payload || typeof payload !== 'object' || payload.webhook_token !== expectedToken) {
      return new Response(JSON.stringify({ error: 'invalid webhook token' }), {
        status: 401,
        headers: { 'content-type': 'application/json' },
      });
    }

    const {
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

    const eventId = event_id || `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const receivedAt = new Date().toISOString();

    if (env.ROLLING_KV) {
      const storageKey = `signal:${symbol}:${cycle_code}:${signal}`;
      const latestKey = `latest:${symbol}`;
      
      const record = {
        symbol,
        cycle_code,
        signal,
        trigger_time_utc,
        event_id: eventId,
        received_at: receivedAt,
      };

      // 存储当前信号与最新快照
      await Promise.all([
        env.ROLLING_KV.put(storageKey, JSON.stringify(record)),
        env.ROLLING_KV.put(latestKey, JSON.stringify(record)),
      ]);

      // 1分钟内重复信号防抖与 Telegram 实时提醒 (防暴击)
      const notifyLockKey = `notify_lock:${symbol}:${cycle_code}:${signal}`;
      const hasRecentNotify = await env.ROLLING_KV.get(notifyLockKey);

      if (!hasRecentNotify) {
        // 设置 60s (1分钟) 冷却锁
        await env.ROLLING_KV.put(notifyLockKey, '1', { expirationTtl: 60 });

        const tgToken = env.TELEGRAM_BOT_TOKEN;
        const chatId = env.TELEGRAM_CHAT_ID;

        if (tgToken && chatId) {
          const signalEmoji = signal === 'BUY' ? '🔴【多头买入信号】' : '🟢【空方卖出预警】';
          const timeStr = new Date(receivedAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
          const text = `${signalEmoji}\n\n• 标的：${symbol}\n• 节点：${cycle_code}\n• 动作：${signal}\n• 时间：${timeStr}\n• 事件ID：${eventId.slice(0, 12)}\n\n🔗 终端：https://etf.peekabo.cc/a-rolling/`;

          try {
            await fetch(`https://api.telegram.org/bot${tgToken}/sendMessage`, {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
            });
          } catch (e) {
            console.warn('Telegram alert failed:', e);
          }
        }
      }
    }

    return new Response(
      JSON.stringify({
        success: true,
        message: `Signal ${signal} for cycle ${cycle_code} accepted`,
        event_id: eventId,
        received_at: receivedAt,
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
