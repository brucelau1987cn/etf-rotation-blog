/**
 * POST /api/v1/tradingview
 * Receive TradingView webhook and store first-write-wins daily signals in D1.
 * No KV required for the public board path.
 */

import {
  insertRollingSignalOnce,
  normalizeSymbol,
  normalizeTriggerPrice,
  shanghaiTradeDate,
  updateRollingSignalPriceIfMissing,
} from '../../_lib/rolling-signals-d1.js';

const formatSignalTime = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || '');
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
};

const formatCycle = (cycleCode) => {
  const code = String(cycleCode || '').trim();
  if (/^\d+(?:\.\d+)?h$/i.test(code)) return `${code.slice(0, -1)}小时`;
  if (/^\d+(?:\.\d+)?m$/i.test(code)) return `${code.slice(0, -1)}分钟`;
  return code;
};

const resolveInstrumentName = async ({ env, requestUrl, symbol, instrumentName }) => {
  const supplied = String(instrumentName || '').trim();
  if (supplied) return supplied;
  if (!env.ASSETS || typeof env.ASSETS.fetch !== 'function') return '';
  try {
    const assetUrl = new URL('/data/a-rolling-instruments.json', requestUrl);
    const response = await env.ASSETS.fetch(new Request(assetUrl));
    if (!response.ok) return '';
    const payload = await response.json();
    const match = (payload.instruments || []).find((item) => normalizeSymbol(item.symbol) === symbol);
    return String(match?.instrument_name || '').trim();
  } catch (error) {
    console.warn('ntfy instrument lookup failed:', error);
    return '';
  }
};

const KLINE_LOOKUP_BASES = [
  // Dedicated Worker avoids recursive Pages Function calls and is faster in production.
  'https://edge-quote-api.brucelau1987.workers.dev',
  'https://etf.peekabo.cc',
];

/** Prefer webhook-provided price; otherwise resolve 1m close at trigger time via edge kline. */
export const resolveTriggerPrice = async ({
  payload = {},
  symbol,
  triggerTime,
  requestUrl,
  fetchImpl = fetch,
}) => {
  const direct = normalizeTriggerPrice(
    payload.price
      ?? payload.trigger_price
      ?? payload.close
      ?? payload.trigger_close,
  );
  if (direct != null) {
    return {
      price: direct,
      source: String(payload.price_source || payload.trigger_price_source || 'webhook').trim() || 'webhook',
    };
  }

  // US/HK/futures can still store webhook price later; 1m edge kline is A-share/ETF first.
  if (!/^\d{6}$/.test(String(symbol || ''))) {
    return { price: null, source: null };
  }

  const bases = [...KLINE_LOOKUP_BASES];
  try {
    const requestOrigin = requestUrl ? new URL(requestUrl).origin : '';
    if (requestOrigin && !bases.includes(requestOrigin)) bases.push(requestOrigin);
  } catch {
    // ignore invalid request URL
  }

  for (const base of bases) {
    try {
      const klineUrl = new URL('/api/public/v1/kline', base);
      klineUrl.searchParams.set('symbol', symbol);
      if (triggerTime) klineUrl.searchParams.set('at', String(triggerTime));
      klineUrl.searchParams.set('nocache', '1');
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4500);
      const response = await fetchImpl(klineUrl.toString(), {
        headers: { Accept: 'application/json', 'User-Agent': 'HermesRollingWebhook/1.0' },
        signal: controller.signal,
      }).finally(() => clearTimeout(timer));
      if (!response.ok) continue;
      const data = await response.json();
      const close = normalizeTriggerPrice(data?.bar?.close);
      if (close == null) continue;
      return {
        price: close,
        source: String(data?.bar?.source || data?.source || 'kline-1m'),
      };
    } catch (error) {
      console.warn('trigger price kline lookup failed:', base, error?.message || error);
    }
  }
  return { price: null, source: null };
};

export const formatPushPriceLine = (triggerPrice, { bullet = false } = {}) => {
  const prefix = bullet ? '• 信号点股价：' : '信号点股价：';
  if (Number.isFinite(Number(triggerPrice)) && Number(triggerPrice) > 0) {
    return `${prefix}¥${Number(triggerPrice).toFixed(2)}`;
  }
  return `${prefix}暂无`;
};

export const sendNtfySignal = async ({
  env,
  fetchImpl,
  requestUrl,
  symbol,
  instrumentName,
  cycleCode,
  signal,
  triggerTime,
  triggerPrice = null,
}) => {
  const pushUrl = String(env.NTFY_PUSH_URL || '').trim();
  if (!pushUrl) return false;

  const direction = signal === 'BUY' ? '多方信号' : '空方信号';
  const resolvedName = await resolveInstrumentName({ env, requestUrl, symbol, instrumentName });
  const titleTarget = [resolvedName, symbol].filter(Boolean).join(' ');
  const publishUrl = new URL(pushUrl);
  const pathParts = publishUrl.pathname.split('/').filter(Boolean);
  const topic = decodeURIComponent(pathParts.pop() || '');
  if (!topic) throw new Error('NTFY_PUSH_URL must include a topic path');
  publishUrl.pathname = pathParts.length ? `/${pathParts.join('/')}/` : '/';
  publishUrl.search = '';
  publishUrl.hash = '';

  const response = await fetchImpl(publishUrl.toString(), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      topic,
      title: `${direction}｜${titleTarget}`,
      message: [
        `时间：${formatSignalTime(triggerTime)}`,
        `节点：${formatCycle(cycleCode)}`,
        `方向：${direction}`,
        formatPushPriceLine(triggerPrice),
      ].join('\n'),
      priority: 4,
      tags: [signal === 'BUY' ? 'chart_with_upwards_trend' : 'chart_with_downwards_trend'],
      click: 'https://etf.peekabo.cc/rolling/',
    }),
  });
  if (!response.ok) throw new Error(`ntfy returned HTTP ${response.status}`);
  return true;
};

export const sendWxPusherSignal = async ({
  env,
  fetchImpl = fetch,
  requestUrl,
  symbol,
  instrumentName,
  cycleCode,
  signal,
  triggerTime,
  triggerPrice = null,
}) => {
  const appToken = String(env.WXPUSHER_APP_TOKEN || '').trim();
  const uid = String(env.WXPUSHER_UID || env.WXPUSHER_UIDS || '').trim();
  const topicId = String(env.WXPUSHER_TOPIC_ID || '').trim();
  if (!appToken || (!uid && !topicId)) return false;

  const direction = signal === 'BUY' ? '多方信号' : '空方信号';
  const resolvedName = await resolveInstrumentName({ env, requestUrl, symbol, instrumentName });
  const titleTarget = [resolvedName, symbol].filter(Boolean).join(' ');
  const signalEmoji = signal === 'BUY' ? '🔴' : '🟢';

  const priceStr = Number.isFinite(Number(triggerPrice)) && Number(triggerPrice) > 0
    ? `¥${Number(triggerPrice).toFixed(2)}`
    : '暂无';

  const title = `${signalEmoji} ${direction}｜${titleTarget}`;
  const summary = `${title} · ${priceStr}`;

  const message = [
    `### ${title}`,
    '',
    `- **标的**：${titleTarget}`,
    `- **节点**：${formatCycle(cycleCode)}`,
    `- **方向**：${direction}`,
    `- **时间**：${formatSignalTime(triggerTime)}`,
    `- **信号点股价**：${priceStr}`,
    '',
    `[点击进入终端](https://etf.peekabo.cc/rolling/)`,
  ].join('\n');

  const body = {
    appToken,
    content: message,
    summary,
    contentType: 3, // Markdown
    url: 'https://etf.peekabo.cc/rolling/',
  };
  if (uid) {
    body.uids = uid.split(',').map((s) => s.trim()).filter(Boolean);
  }
  if (topicId) {
    body.topicIds = topicId.split(',').map((s) => Number(s.trim())).filter((n) => Number.isFinite(n));
  }

  const response = await fetchImpl('https://wxpusher.zjiecode.com/api/send/message', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`WxPusher returned HTTP ${response.status}`);
  const result = await response.json();
  if (result.code !== 1000) throw new Error(`WxPusher error ${result.code}: ${result.msg}`);
  return true;
};

export async function onRequestPost({ request, env, waitUntil, fetch: fetchImpl = fetch }) {
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
      symbol: rawSymbol = '600021',
      cycle_code,
      signal,
      trigger_time_utc = new Date().toISOString(),
      event_id,
      instrument_name = null,
      exchange = null,
    } = payload;
    const symbol = normalizeSymbol(rawSymbol);

    if (!cycle_code || !['BUY', 'SELL'].includes(signal)) {
      return new Response(JSON.stringify({ error: 'missing cycle_code or invalid signal type' }), {
        status: 422,
        headers: { 'content-type': 'application/json' },
      });
    }

    if (!env.DB) {
      return new Response(JSON.stringify({ error: 'DB missing on server' }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
      });
    }

    const eventId = event_id || `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const receivedAt = new Date().toISOString();
    const triggerTime = trigger_time_utc || receivedAt;
    const tradeDate = shanghaiTradeDate(triggerTime);
    // Webhook-provided prices are instant. External 1m lookup is deferred so TradingView
    // receives HTTP 200 well inside its ~3-second timeout window.
    const directPrice = normalizeTriggerPrice(
      payload.price ?? payload.trigger_price ?? payload.close ?? payload.trigger_close,
    );
    const directSource = directPrice == null
      ? null
      : (String(payload.price_source || payload.trigger_price_source || 'webhook').trim() || 'webhook');

    const saved = await insertRollingSignalOnce(env.DB, {
      trade_date: tradeDate,
      symbol,
      cycle_code,
      signal,
      trigger_time_utc: triggerTime,
      received_at: receivedAt,
      event_id: eventId,
      label: String(cycle_code),
      instrument_name,
      exchange,
      trigger_price: directPrice,
      trigger_price_source: directSource,
    });

    // First write returns immediately. Missing price resolution and all notifications run
    // under waitUntil, then the exact inserted row is filled only while price is still null.
    if (saved.inserted) {
      const finishSignal = (async () => {
        let finalRow = saved.row;
        let finalPrice = saved.row?.trigger_price ?? directPrice;
        if (finalPrice == null) {
          const resolved = await resolveTriggerPrice({
            payload,
            symbol,
            triggerTime,
            requestUrl: request.url,
            fetchImpl,
          });
          if (resolved.price != null) {
            const updated = await updateRollingSignalPriceIfMissing(env.DB, {
              trade_date: tradeDate,
              symbol,
              cycle_code,
              signal,
              event_id: saved.row?.event_id || eventId,
              trigger_price: resolved.price,
              trigger_price_source: resolved.source,
            });
            finalRow = updated.row || finalRow;
            finalPrice = finalRow?.trigger_price ?? resolved.price;
          }
        }

        const instrumentName = finalRow?.instrument_name || instrument_name;
        await Promise.allSettled([
          sendNtfySignal({
            env, fetchImpl, requestUrl: request.url, symbol, instrumentName,
            cycleCode: cycle_code, signal, triggerTime, triggerPrice: finalPrice,
          }),
          sendWxPusherSignal({
            env, fetchImpl, requestUrl: request.url, symbol, instrumentName,
            cycleCode: cycle_code, signal, triggerTime, triggerPrice: finalPrice,
          }),
          (async () => {
            const tgToken = env.TELEGRAM_BOT_TOKEN;
            const chatId = env.TELEGRAM_CHAT_ID;
            if (!tgToken || !chatId) return false;
            const signalEmoji = signal === 'BUY' ? '🔴【多头买入信号】' : '🟢【空方卖出预警】';
            const timeStr = new Date(receivedAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
            const text = [
              signalEmoji, '', `• 标的：${symbol}`, `• 节点：${cycle_code}`, `• 动作：${signal}`,
              `• 交易日：${tradeDate}`, `• 时间：${timeStr}`,
              formatPushPriceLine(finalPrice, { bullet: true }),
              `• 事件ID：${eventId.slice(0, 12)}`, '',
              '🔗 终端：https://etf.peekabo.cc/rolling/',
            ].join('\n');
            const response = await fetchImpl(`https://api.telegram.org/bot${tgToken}/sendMessage`, {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
            });
            if (!response.ok) throw new Error(`Telegram returned HTTP ${response.status}`);
            return true;
          })(),
        ]);
        return true;
      })().catch((error) => {
        console.warn('webhook background completion failed:', error);
        return false;
      });
      if (typeof waitUntil === 'function') waitUntil(finishSignal);
      else await finishSignal;
    }

    return new Response(
      JSON.stringify({
        success: true,
        message: saved.inserted
          ? `Signal ${signal} for cycle ${cycle_code} accepted`
          : `Signal ${signal} for cycle ${cycle_code} already locked for ${tradeDate}`,
        event_id: saved.row?.event_id || eventId,
        received_at: saved.row?.received_at || receivedAt,
        trade_date: tradeDate,
        inserted: saved.inserted,
        storage: 'd1',
        trigger_price: saved.row?.trigger_price ?? directPrice ?? null,
        trigger_price_source: saved.row?.trigger_price_source ?? directSource ?? null,
        price_resolution: (saved.row?.trigger_price ?? directPrice) == null ? 'background' : 'complete',
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
