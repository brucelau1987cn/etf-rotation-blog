// buy-stock page controller: loads holdings, pulls live quotes, and renders
// fee-adjusted P&L.
//
// The fee model lives in ./buy-stock-fees.js — one canonical copy, imported
// by this file, the Astro page, and the node test suite.

import {
  DEFAULT_FEES,
  computeBuyFees,
  computeSellFees,
  round2,
} from './buy-stock-fees.js';

const HOLDINGS_URL = '/api/public/v1/buy-stock';
const QUOTE_URL = '/api/public/v1/quote';
const QUOTE_INTERVAL_MS = 30000;

const state = {
  positions: [],
  quotes: {},
  fees: { ...DEFAULT_FEES },
  loading: false,
  error: '',
  lastQuoteAt: null,
};

const el = (id) => document.getElementById(id);

const money = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const signed = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const num = Number(value);
  return `${num > 0 ? '+' : ''}${money(num)}`;
};

const pct = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const num = Number(value);
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
};

const dirClass = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
  return Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : '';
};

/**
 * Compute the fee-aware economics of one position.
 */
function computePosition(position) {
  const shares = Number(position.shares) || 0;
  const buyPrice = Number(position.buy_price) || 0;
  const buyTurnover = buyPrice * shares;
  const buyCost = computeBuyFees(buyTurnover, state.fees);
  const cost = round2(buyTurnover + buyCost.total);

  const price = Number(state.quotes[position.code]?.price) || 0;
  const quote = state.quotes[position.code] || null;
  const hasPrice = price > 0;
  const marketValue = hasPrice ? round2(price * shares) : null;
  const sellCost = marketValue === null ? null : computeSellFees(marketValue, state.fees);

  const unrealizedPnl = marketValue === null ? null : round2(marketValue - cost);
  const unrealizedPct = marketValue === null || cost === 0
    ? null
    : round2(((marketValue - cost) / cost) * 100);

  return {
    ...position,
    price,
    change_percent: quote?.change_percent === undefined ? null : Number(quote.change_percent),
    quote_time: quote?.quote_time || null,
    marketValue,
    cost,
    buyTurnover: round2(buyTurnover),
    buyCost,
    sellCost,
    unrealizedPnl,
    unrealizedPct,
  };
}

function renderSummary(rows) {
  const open = rows.filter((row) => !row.sold_at);
  const totalMarketValue = round2(open.reduce((sum, row) => sum + (row.marketValue || 0), 0));
  const totalCost = round2(open.reduce((sum, row) => sum + row.cost, 0));
  const totalPnl = round2(open.reduce((sum, row) => sum + (row.unrealizedPnl || 0), 0));
  const totalPnlPct = totalCost === 0 ? null : round2(((totalMarketValue - totalCost) / totalCost) * 100);

  const set = (id, value) => {
    const node = el(id);
    if (node) node.textContent = value;
  };
  set('bs-stat-count', String(open.length));
  set('bs-stat-cost', `¥${money(totalCost)}`);
  set('bs-stat-value', `¥${money(totalMarketValue)}`);
  set('bs-stat-pnl', `¥${signed(totalPnl)}`);
  const pnlNode = el('bs-stat-pnl');
  if (pnlNode) pnlNode.className = `bs-stat-value ${dirClass(totalPnl)}`;
  const pctNode = el('bs-stat-pnl-pct');
  if (pctNode) {
    pctNode.textContent = pct(totalPnlPct);
    pctNode.className = `bs-stat-sub ${dirClass(totalPnl)}`;
  }
  const costNode = el('bs-stat-cost');
  if (costNode) costNode.className = 'bs-stat-value';
  const valueNode = el('bs-stat-value');
  if (valueNode) valueNode.className = 'bs-stat-value';
}

function positionRow(row) {
  const quoteCell = row.price > 0
    ? `<span class="bs-price">${money(row.price)}</span><span class="bs-change ${dirClass(row.change_percent)}">${pct(row.change_percent)}</span>`
    : '<span class="bs-muted">休市</span>';

  const feeText = row.sellCost
    ? `买 ${money(row.buyCost.total)} / 卖 ${money(row.sellCost.total)}`
    : `买 ${money(row.buyCost.total)}`;

  return `
    <tr data-code="${row.code}">
      <td>
        <span class="bs-code">${row.code}</span>
        <span class="bs-name">${row.name}</span>
        ${row.note ? `<span class="bs-note">${row.note}</span>` : ''}
      </td>
      <td class="bs-num">${money(row.buy_price)}</td>
      <td class="bs-num">${row.shares}</td>
      <td class="bs-num">${money(row.buyTurnover)}</td>
      <td class="bs-num bs-muted">${feeText}</td>
      <td class="bs-num">${quoteCell}</td>
      <td class="bs-num">${row.marketValue === null ? '—' : money(row.marketValue)}</td>
      <td class="bs-num bs-strong">${money(row.cost)}</td>
      <td class="bs-num bs-strong ${dirClass(row.unrealizedPnl)}">${row.unrealizedPnl === null ? '—' : signed(row.unrealizedPnl)}</td>
      <td class="bs-num ${dirClass(row.unrealizedPct)}">${row.unrealizedPct === null ? '—' : pct(row.unrealizedPct)}</td>
      <td class="bs-num bs-muted bs-time">${row.buy_time}</td>
    </tr>`;
}

function render() {
  const rows = state.positions.map(computePosition);
  const open = rows.filter((row) => !row.sold_at);
  const closed = rows.filter((row) => row.sold_at);

  renderSummary(rows);

  const tbody = el('bs-tbody');
  if (tbody) {
    tbody.innerHTML = open.length > 0
      ? open.map(positionRow).join('')
      : '<tr><td colspan="11" class="bs-empty">暂无持仓记录</td></tr>';
  }

  const closedBody = el('bs-tbody-closed');
  if (closedBody) {
    closedBody.innerHTML = closed.length > 0
      ? closed.map(positionRow).join('')
      : '<tr><td colspan="11" class="bs-empty">暂无已卖出记录</td></tr>';
  }

  const closedSection = el('bs-closed-section');
  if (closedSection) closedSection.hidden = closed.length === 0;

  const status = el('bs-status');
  if (status) {
    if (state.error) {
      status.textContent = state.error;
      status.className = 'bs-status bs-error';
    } else if (state.lastQuoteAt) {
      const time = new Date(state.lastQuoteAt).toLocaleTimeString('zh-CN', { hour12: false });
      status.textContent = `行情更新于 ${time}`;
      status.className = 'bs-status';
    } else {
      status.textContent = '正在加载行情…';
      status.className = 'bs-status';
    }
  }
}

async function fetchQuotes() {
  const codes = state.positions.map((item) => item.code).filter(Boolean);
  if (codes.length === 0) {
    state.quotes = {};
    state.lastQuoteAt = Date.now();
    render();
    return;
  }
  try {
    const res = await fetch(`${QUOTE_URL}?symbols=${encodeURIComponent(codes.join(','))}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data && data.status === 'ok') {
      state.quotes = data.quotes || {};
      state.lastQuoteAt = Date.now();
    }
  } catch (error) {
    console.warn('buy-stock quote fetch failed', error);
  }
  render();
}

async function loadPositions() {
  if (state.loading) return;
  state.loading = true;
  try {
    const res = await fetch(HOLDINGS_URL, { credentials: 'same-origin' });
    if (res.status === 401) {
      state.error = '需要登录后查看持仓';
      state.positions = [];
      render();
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || data.ok !== true) throw new Error(data?.error || '数据不可用');
    state.positions = data.positions || [];
    state.error = '';
  } catch (error) {
    state.error = `持仓数据不可用：${error.message}`;
    state.positions = [];
  } finally {
    state.loading = false;
    render();
  }
}

function init() {
  if (!el('bs-tbody')) return;
  loadPositions().then(fetchQuotes);
  setInterval(fetchQuotes, QUOTE_INTERVAL_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') fetchQuotes();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
