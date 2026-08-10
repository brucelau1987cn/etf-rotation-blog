/**
 * Futures compass snapshot + Edge quote overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
const SNAPSHOT_URL = '/data/futures-compass.json';
const EDGE_QUOTE_URL = '/api/public/v1/quote';
const WATCHLIST_URL = '/api/public/v1/futures-watchlist';

// Fallback mapping if watchlist API is unavailable.
const FALLBACK_EDGE_SYMBOLS = {
  LC: 'nf_LC0', PS: 'nf_PS0', SI: 'nf_SI0', AU: 'nf_AU0', AG: 'nf_AG0',
  CU: 'nf_CU0', AL: 'nf_AL0', SC: 'nf_SC0', LH: 'nf_LH0', JM: 'nf_JM0',
};

let EDGE_SYMBOLS = { ...FALLBACK_EDGE_SYMBOLS };
const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const priceDigits = (tick) => Number(tick) >= 1 ? 0 : Number(tick) >= .1 ? 1 : 2;
const quote = (value, tick) => number(value, priceDigits(tick));
const pct = (value) => Number.isFinite(Number(value)) ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%` : '—';
const multiple = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}×` : '—';
const tone = (value) => Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : 'flat';
const stateTone = (value) => value?.includes('增仓上涨') ? 'bull' : value?.includes('增仓下跌') ? 'bear' : value?.includes('减仓') ? 'caution' : 'neutral';

let loading = false;
const button = document.getElementById('refresh-button');
const status = document.getElementById('refresh-status');
const adapter = window.EtfQuote || null;
const grid = document.querySelector('.watch-grid');

function leader(item, field = 'change_pct') { return item ? `${item.code} ${item.name} · ${pct(item[field])}` : '等待有效数据'; }

function ensureCard(item) {
  if (!grid) return null;
  let card = grid.querySelector(`[data-code="${item.code}"]`);
  if (card) return card;
  card = document.createElement('article');
  card.className = `future-card ${tone(item.change_pct)}`;
  card.dataset.code = item.code;
  card.innerHTML = `
    <div class="card-top">
      <div>
        <strong class="name">${item.name || item.code}</strong>
        <span class="contract-name">实际主力 ${item.contract_code || '待确认'} · ${item.exchange || ''}</span>
      </div>
      <div class="px">
        <b class="price">${quote(item.price, item.tick)}</b>
        <em class="change">${pct(item.change_pct)}</em>
      </div>
    </div>
    <div class="evidence-grid">
      <div class="evidence"><span>开盘</span><strong class="open">${quote(item.open, item.tick)}</strong></div>
      <div class="evidence"><span>最高</span><strong class="high">${quote(item.high, item.tick)}</strong></div>
      <div class="evidence"><span>最低</span><strong class="low">${quote(item.low, item.tick)}</strong></div>
      <div class="evidence"><span>成交量</span><strong class="volume">${number(item.volume, 0)}</strong></div>
      <div class="evidence"><span>持仓</span><strong class="open-interest">${number(item.open_interest, 0)}</strong></div>
      <div class="evidence"><span>持仓变化</span><strong class="oi-change">${number(item.open_interest_change, 0)} · ${pct(item.open_interest_change_pct)}</strong></div>
      <div class="evidence"><span>趋势</span><strong class="trend-state">${item.trend_state || '未知'}</strong></div>
      <div class="evidence"><span>结构</span><strong class="structure">${item.structure || '未知'}</strong></div>
      <div class="evidence"><span>20日位置</span><strong class="range-position">${pct(item.range_20d_position_pct)}</strong></div>
      <div class="evidence"><span>支撑</span><strong class="support">${quote(item.support, item.tick)}</strong></div>
      <div class="evidence"><span>压力</span><strong class="resistance">${quote(item.resistance, item.tick)}</strong></div>
      <div class="evidence"><span>量比</span><strong class="volume-ratio">${multiple(item.volume_ratio_20d)}</strong></div>
    </div>
    <div class="card-foot">
      <span><b class="capital-state ${stateTone(item.capital_state)}">${item.capital_state || '量仓未知'}</b> · <span class="fvg-state">${item.fvg?.direction || 'FVG未知'}</span></span>
      <span class="quote-time">${item.quote_time || '—'}</span>
    </div>`;
  grid.appendChild(card);
  return card;
}

function render(payload) {
  const items = payload.items || [];
  const keep = new Set(items.map((item) => item.code));
  if (grid) {
    for (const card of [...grid.querySelectorAll('[data-code]')]) {
      if (!keep.has(card.getAttribute('data-code'))) card.remove();
    }
  }
  for (const item of items) {
    const card = ensureCard(item);
    if (!card) continue;
    card.classList.remove('up', 'down', 'flat');
    card.classList.add(tone(item.change_pct));
    const nameNode = card.querySelector('.name');
    if (nameNode) nameNode.textContent = item.name || item.code;
    const values = {
      '.contract-name': `实际主力 ${item.contract_code || '待确认'} · ${item.exchange || ''}`,
      '.price': quote(item.price, item.tick),
      '.change': pct(item.change_pct),
      '.open': quote(item.open, item.tick),
      '.high': quote(item.high, item.tick),
      '.low': quote(item.low, item.tick),
      '.volume': number(item.volume, 0),
      '.open-interest': number(item.open_interest, 0),
      '.oi-change': `${number(item.open_interest_change, 0)} · ${pct(item.open_interest_change_pct)}`,
      '.trend-state': item.trend_state || '未知',
      '.structure': item.structure || '未知',
      '.range-position': pct(item.range_20d_position_pct),
      '.support': quote(item.support, item.tick),
      '.resistance': quote(item.resistance, item.tick),
      '.volume-ratio': multiple(item.volume_ratio_20d),
      '.capital-state': item.capital_state || '量仓未知',
      '.fvg-state': item.fvg?.direction || 'FVG未知',
      '.quote-time': item.quote_time || '—',
    };
    for (const [selector, value] of Object.entries(values)) {
      const node = card.querySelector(selector);
      if (node) node.textContent = value;
    }
    const capital = card.querySelector('.capital-state');
    if (capital) {
      capital.className = `capital-state ${stateTone(item.capital_state)}`;
    }
  }
  const sourceLabel = document.getElementById('source-label');
  const dataTime = document.getElementById('data-time');
  if (sourceLabel) sourceLabel.textContent = payload.source || '新浪期货';
  if (dataTime) dataTime.textContent = (payload.generated_at || '—').replace('T', ' ').replace('+08:00', '').slice(0, 16);
  if (status) {
    status.innerHTML = payload.stale
      ? '<strong>快照保留</strong> · 实时源暂时波动'
      : `<strong>${payload.live_overlay ? '实时叠加' : '快照数据'}</strong> · ${payload.cache_age_s > 0 ? `缓存 ${Math.round(payload.cache_age_s)} 秒` : '刚刚更新'}`;
  }
  // leaders
  const ranked = [...items].filter((item) => Number.isFinite(Number(item.change_pct))).sort((a, b) => Number(b.change_pct) - Number(a.change_pct));
  const strongest = document.getElementById('leader-strongest');
  const weakest = document.getElementById('leader-weakest');
  if (strongest) strongest.textContent = leader(ranked[0]);
  if (weakest) weakest.textContent = leader(ranked[ranked.length - 1]);
}

async function loadWatchlistSymbols() {
  try {
    const res = await fetch(`${WATCHLIST_URL}?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    if (!data?.ok || !Array.isArray(data.items)) return;
    const map = {};
    for (const item of data.items) {
      if (!item?.code) continue;
      map[item.code] = item.edge_symbol || `nf_${item.continuous || `${item.code}0`}`;
    }
    if (Object.keys(map).length) EDGE_SYMBOLS = map;
  } catch (err) {
    console.warn('futures watchlist load failed', err);
  }
}

async function loadSnapshot() {
  const response = await fetch(`${SNAPSHOT_URL}?t=${Date.now()}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
  return response.json();
}

async function overlayEdgeQuotes(snapshot) {
  if (!adapter?.normalizeQuotePayload) return snapshot;
  const codes = (snapshot.items || []).map((item) => item.code).filter((code) => EDGE_SYMBOLS[code] || item.edge_symbol || item.continuous);
  if (!codes.length) return snapshot;
  const symbols = codes.map((code) => {
    const item = (snapshot.items || []).find((row) => row.code === code);
    return EDGE_SYMBOLS[code] || item?.edge_symbol || `nf_${item?.continuous || `${code}0`}`;
  }).join(',');
  const res = await fetch(`${EDGE_QUOTE_URL}?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) return snapshot;
  const normalized = adapter.normalizeQuotePayload(await res.json());
  if (!normalized.ok || !normalized.items.length) return snapshot;
  const byEdge = new Map(normalized.items.map((item) => [String(item.symbol || item.code), item]));
  let hit = 0;
  const items = (snapshot.items || []).map((item) => {
    const edgeKey = EDGE_SYMBOLS[item.code] || item.edge_symbol || `nf_${item.continuous || `${item.code}0`}`;
    const edge = byEdge.get(edgeKey);
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
    if (force || !Object.keys(EDGE_SYMBOLS).length) await loadWatchlistSymbols();
    let payload = await loadSnapshot();
    try {
      payload = await overlayEdgeQuotes(payload);
    } catch (overlayError) {
      console.warn('futures edge overlay failed', overlayError);
    }
    render(payload);
  } catch {
    if (status) status.innerHTML = '<strong>静态快照</strong> · 实时连接失败，页面保留最近数据';
  } finally {
    loading = false;
    button?.classList.remove('loading');
    button?.removeAttribute('disabled');
  }
}

button?.addEventListener('click', () => refresh(true));
loadWatchlistSymbols().finally(() => {
  if (window.EtfLivePoll?.startLivePoll) {
    window.EtfLivePoll.startLivePoll({ intervalMs: 60_000, tick: () => refresh(false) });
  } else {
    refresh(false);
    setInterval(() => refresh(false), 60000);
  }
});
