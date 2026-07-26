/**
 * A-share compass live overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
const EDGE_QUOTE_URL = '/api/public/v1/quote';
const liveStatus = document.getElementById('live-quote-status');
let liveLoading = false;
const liveCards = [...document.querySelectorAll('[data-live-card]')];
const { normalizeQuotePayload, aShareSymbolsParam } = window.EtfQuote || {};
const numberOf = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const shanghaiClock = () => {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return { weekday: parts.weekday, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
};
const isTradingWindow = () => {
  const { weekday, minutes } = shanghaiClock();
  return !['Sat', 'Sun'].includes(weekday) && minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 5;
};
const displaySource = (source) => String(source || 'stock-api').replace('@', ' v');
const setStatus = (text, state = '') => {
  if (!liveStatus) return;
  liveStatus.textContent = text;
  liveStatus.classList.toggle('online', state === 'online');
  liveStatus.classList.toggle('error', state === 'error');
};
function applyLive(payload) {
  const liveMap = new Map((payload.items || []).map(item => [String(item.code), item]));
  const time = String(payload.generated_at || '').slice(11, 19) || '刚刚';
  const quoteMode = isTradingWindow() ? '盘中实时' : '最新快照';
  let updated = 0;
  for (const card of liveCards) {
    const code = card.dataset.code;
    const quote = liveMap.get(code);
    const price = numberOf(quote?.price);
    if (price === null) continue;
    const side = card.dataset.side;
    const actionLevel = numberOf(card.dataset.actionLevel);
    const stop = numberOf(card.dataset.stop);
    const priceNode = document.getElementById(`live-price-${code}`);
    const distanceNode = document.getElementById(`live-distance-${code}`);
    const metaNode = document.getElementById(`live-meta-${code}`);
    const triggerNode = document.getElementById(`live-trigger-${code}`);
    if (priceNode) {
      priceNode.textContent = price.toFixed(3);
      priceNode.classList.add('updated');
    }
    let triggered = false;
    let triggerText = '等待动作位';
    if (stop !== null && price <= stop) {
      triggered = true;
      triggerText = '破位撤退';
    } else if (actionLevel !== null && actionLevel > 0) {
      const gap = (price / actionLevel - 1) * 100;
      if (distanceNode) distanceNode.textContent = `${gap >= 0 ? '高于' : '低于'}动作位 ${Math.abs(gap).toFixed(1)}%`;
      if (side === 'harvest') {
        triggered = price >= actionLevel;
        triggerText = triggered ? '达到兑现位' : (Math.abs(gap) <= 3 ? '距兑现位≤3%' : '等待兑现触发');
      } else {
        triggered = price <= actionLevel;
        triggerText = triggered ? '进入伏击区' : (Math.abs(gap) <= 3 ? '距伏击位≤3%' : '等待伏击触发');
      }
    }
    if (triggerNode) {
      triggerNode.textContent = triggerText;
      triggerNode.classList.toggle('triggered', triggered);
    }
    if (metaNode) {
      const change = numberOf(quote?.change_pct);
      const changeText = change === null ? '' : ` · <span class="${change > 0 ? 'market-up' : change < 0 ? 'market-down' : ''}">${change > 0 ? '+' : ''}${change.toFixed(2)}%</span>`;
      metaNode.innerHTML = `${quoteMode} ${time} · ${displaySource(quote?.source || payload.source)}${changeText}`;
    }
    updated += 1;
  }
  setStatus(`${quoteMode} ${time} · ${updated}/${liveCards.length}`, 'online');
}
async function loadLive() {
  if (liveLoading || document.hidden) return;
  liveLoading = true;
  try {
    if (!normalizeQuotePayload || !aShareSymbolsParam) {
      throw new Error('EtfQuote adapter missing');
    }
    const codes = Array.from(liveCards).map(c => c.dataset.code).filter(Boolean);
    if (!codes.length) {
      setStatus('无实时标的', 'error');
      return;
    }
    const symbolsParam = aShareSymbolsParam(codes);
    const edgeRes = await fetch(`${EDGE_QUOTE_URL}?symbols=${encodeURIComponent(symbolsParam)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!edgeRes.ok) throw new Error(`Edge HTTP ${edgeRes.status}`);
    const normalized = normalizeQuotePayload(await edgeRes.json());
    if (!normalized.ok || !normalized.items.length) throw new Error('Edge 返回空行情');
    const items = normalized.items
      .filter((q) => Number(q.price) > 0)
      .map((q) => ({
        code: q.code || q.symbol,
        price: q.price,
        change_pct: q.change_pct ?? q.change_percent,
        source: q.source || 'Cloudflare-Edge-Quote',
      }));
    if (!items.length) throw new Error('Edge 价格无效');
    applyLive({
      ok: true,
      generated_at: normalized.generated_at || new Date().toISOString(),
      source: normalized.source || 'Cloudflare-Edge',
      items,
    });
  } catch (error) {
    setStatus(`实时行情暂不可用 · 保留静态快照`, 'error');
    console.warn('ETF live quote overlay failed', error);
  } finally {
    liveLoading = false;
  }
}

void loadLive();
if (window.EtfLivePoll?.startLivePoll) {
  window.EtfLivePoll.startLivePoll({ intervalMs: 30_000, immediate: false, tick: loadLive });
} else {
  window.setInterval(loadLive, 30_000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void loadLive();
  });
}
    
