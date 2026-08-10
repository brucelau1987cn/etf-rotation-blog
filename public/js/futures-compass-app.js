/**
 * Futures compass snapshot + Edge quote overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
const SNAPSHOT_URL = '/data/futures-compass.json';
const EDGE_QUOTE_URL = '/api/public/v1/quote';
// continuous -> Edge futures symbol (Sina/Tencent nf_ prefix)
const EDGE_SYMBOLS = {
  LC: 'nf_LC0',
  PS: 'nf_PS0',
  SI: 'nf_SI0',
  AU: 'nf_AU0',
  AG: 'nf_AG0',
  CU: 'nf_CU0',
  AL: 'nf_AL0',
  SC: 'nf_SC0',
  LH: 'nf_LH0',
  JM: 'nf_JM0',
};
const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const priceDigits = (tick) => Number(tick) >= 1 ? 0 : Number(tick) >= .1 ? 1 : 2;
const quote = (value, tick) => number(value, priceDigits(tick));
const pct = (value) => Number.isFinite(Number(value)) ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%` : '—';
const multiple = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}×` : '—';
let loading = false;
const button = document.getElementById('refresh-button');
const status = document.getElementById('refresh-status');
const adapter = window.EtfQuote || null;
function leader(item, field = 'change_pct') { return item ? `${item.code} ${item.name} · ${pct(item[field])}` : '等待有效数据'; }
function render(payload) {
  for (const item of payload.items || []) {
    const card = document.querySelector(`[data-code="${item.code}"]`); if (!card) continue;
    card.classList.remove('up','down','flat'); card.classList.add(Number(item.change_pct)>0?'up':Number(item.change_pct)<0?'down':'flat');
    const values = {'.contract-name':`实际主力 ${item.contract_code||'待确认'} · ${item.exchange||''}`,'.price':quote(item.price,item.tick),'.change':pct(item.change_pct),'.open':quote(item.open,item.tick),'.high':quote(item.high,item.tick),'.low':quote(item.low,item.tick),'.volume':number(item.volume,0),'.open-interest':number(item.open_interest,0),'.oi-change':`${number(item.open_interest_change,0)} · ${pct(item.open_interest_change_pct)}`,'.trend-state':item.trend_state||'未知','.structure':item.structure||'未知','.range-position':pct(item.range_20d_position_pct),'.support':quote(item.support,item.tick),'.resistance':quote(item.resistance,item.tick),'.volume-ratio':multiple(item.volume_ratio_20d),'.capital-state':item.capital_state||'量仓未知','.fvg-state':item.fvg?.direction||'FVG未知','.quote-time':item.quote_time||'—'};
    for (const [selector,value] of Object.entries(values)) { const node=card.querySelector(selector); if(node) node.textContent=value; }
  }
  document.getElementById('source-label').textContent=payload.source||'新浪期货'; document.getElementById('data-time').textContent=(payload.generated_at||'—').replace('T',' ').replace('+08:00','').slice(0,16);
  status.innerHTML=payload.stale?'<strong>快照保留</strong> · 实时源暂时波动':`<strong>${payload.live_overlay ? '实时叠加' : '快照数据'}</strong> · ${payload.cache_age_s>0?`缓存 ${Math.round(payload.cache_age_s)} 秒`:'刚刚更新'}`;
}
async function loadSnapshot() {
  const response = await fetch(`${SNAPSHOT_URL}?t=${Date.now()}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
  return response.json();
}
async function overlayEdgeQuotes(snapshot) {
  if (!adapter?.normalizeQuotePayload) return snapshot;
  const codes = (snapshot.items || []).map((item) => item.code).filter((code) => EDGE_SYMBOLS[code]);
  if (!codes.length) return snapshot;
  const symbols = codes.map((code) => EDGE_SYMBOLS[code]).join(',');
  const res = await fetch(`${EDGE_QUOTE_URL}?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) return snapshot;
  const normalized = adapter.normalizeQuotePayload(await res.json());
  if (!normalized.ok || !normalized.items.length) return snapshot;
  const byEdge = new Map(normalized.items.map((item) => [String(item.symbol || item.code), item]));
  let hit = 0;
  const items = (snapshot.items || []).map((item) => {
    const edge = byEdge.get(EDGE_SYMBOLS[item.code]);
    if (!edge || !(Number(edge.price) > 0)) return item;
    hit += 1;
    return {
      ...item,
      price: edge.price,
      open: edge.open ?? item.open,
      high: edge.high ?? item.high,
      low: edge.low ?? item.low,
      prev_close: edge.prev_close ?? item.prev_close,
      change_pct: edge.change_percent ?? edge.change_pct ?? item.change_pct,
      quote_time: edge.quote_time || item.quote_time,
      quote_source: edge.source || normalized.source || 'edge-quote',
    };
  });
  return {
    ...snapshot,
    items,
    source: hit ? `${snapshot.source || '快照'} + Edge` : snapshot.source,
    live_overlay: hit > 0,
    generated_at: snapshot.generated_at || normalized.generated_at,
  };
}
async function refresh(force = false) {
  if (loading) return;
  loading = true;
  button?.classList.add('loading');
  button?.setAttribute('disabled', '');
  try {
    let payload = await loadSnapshot();
    try {
      payload = await overlayEdgeQuotes(payload);
    } catch (overlayError) {
      console.warn('futures edge overlay failed', overlayError);
    }
    render(payload);
  } catch {
    status.innerHTML = '<strong>静态快照</strong> · 实时连接失败，页面保留最近数据';
  } finally {
    loading = false;
    button?.classList.remove('loading');
    button?.removeAttribute('disabled');
  }
}
button?.addEventListener('click', () => refresh(true));
if (window.EtfLivePoll?.startLivePoll) {
  window.EtfLivePoll.startLivePoll({ intervalMs: 60_000, tick: () => refresh(false) });
} else {
  refresh(false);
  setInterval(() => refresh(false), 60000);
}
    
