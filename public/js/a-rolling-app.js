/**
 * A-share rolling multi-instrument board.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
(function() {
  const { normalizeQuotePayload, findQuoteItem } = window.EtfQuote || {};
  if (!normalizeQuotePayload || !findQuoteItem) {
    console.error('EtfQuote adapter missing');
    return;
  }

  const FORMAL_BUY_ORDER = ['2h', '2.5h', '3h', '3.5h', '4h', '4.5h', '5h', '5.5h'];
  const INSTRUMENTS = [
    { name: '上海电力', exchange: 'SSE', symbol: '600021' },
    { name: '创新医疗', exchange: 'SZSE', symbol: '002173' },
    { name: '三安光电', exchange: 'SSE', symbol: '600703' },
  ];

  const formatTime = (value, includeDate = true, includeSeconds = false) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const options = {
      timeZone: 'Asia/Shanghai',
      hour12: false,
      hour: '2-digit',
      minute: '2-digit'
    };
    if (includeSeconds) options.second = '2-digit';
    if (includeDate) {
      options.month = '2-digit';
      options.day = '2-digit';
    }
    return date.toLocaleString('zh-CN', options);
  };

  const orderIndex = (code) => {
    const i = FORMAL_BUY_ORDER.indexOf(code);
    return i === -1 ? 999 : i;
  };

  const normalizeTimeline = (data) => {
    let timeline = Array.isArray(data?.timeline) ? data.timeline.slice() : [];
    if (timeline.length === 0) {
      const buyItems = (data?.cycles || data?.buy_cycles || []).map(c => ({
        type: 'BUY',
        code: c.cycle_code,
        triggered_at: c.buy_triggered_at,
        label: c.label
      }));
      const sellItems = (data?.sell_chain?.nodes || []).map(n => ({
        type: 'SELL',
        code: n.code,
        triggered_at: n.triggered_at,
        label: n.label
      }));
      timeline = [...buyItems, ...sellItems];
    }
    // Drop observation window and sort formal buys by fixed window order.
    const buys = timeline
      .filter(item => item && item.type === 'BUY' && item.code !== '1.75h')
      .sort((a, b) => {
        const oa = orderIndex(a.code);
        const ob = orderIndex(b.code);
        if (oa !== ob) return oa - ob;
        return new Date(a.triggered_at || 0).getTime() - new Date(b.triggered_at || 0).getTime();
      });
    const sells = timeline
      .filter(item => item && item.type === 'SELL')
      .sort((a, b) => new Date(a.triggered_at || 0).getTime() - new Date(b.triggered_at || 0).getTime());
    return { buys, sells };
  };

  const renderCells = (container, items, kind) => {
    if (!container) return;
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-rail';
      empty.textContent = kind === 'BUY' ? '买入信号暂无' : '卖出信号暂无';
      container.appendChild(empty);
      return;
    }
    items.forEach(item => {
      const badge = document.createElement('div');
      badge.className = kind === 'BUY' ? 'cell-badge buy-buy' : `cell-badge sell-${(item.sell_state || 'sell').toLowerCase()}`;
      if (kind === 'BUY') badge.dataset.buyCode = item.code;
      else badge.dataset.sellCode = item.code;
      const codeEl = document.createElement('div');
      codeEl.className = 'badge-code';
      codeEl.textContent = item.code;
      const timeEl = document.createElement('div');
      timeEl.className = 'badge-time';
      timeEl.textContent = formatTime(item.triggered_at, true, false);
      badge.append(codeEl, timeEl);
      container.appendChild(badge);
    });
  };

  const updateBoard = (symbol, data) => {
    const board = document.querySelector(`.instrument-board[data-symbol="${symbol}"]`);
    if (!board || !data) return;
    const nameEl = board.querySelector('[data-role="inst-name"]');
    const symbolEl = board.querySelector('[data-role="inst-symbol"]');
    const metaEl = board.querySelector('[data-role="signal-meta"]');
    if (nameEl && data.instrument?.instrument_name) nameEl.textContent = data.instrument.instrument_name;
    if (symbolEl && data.instrument?.symbol) symbolEl.textContent = data.instrument.symbol;

    const { buys, sells } = normalizeTimeline(data);
    renderCells(board.querySelector('[data-role="buy-cells"]'), buys, 'BUY');
    renderCells(board.querySelector('[data-role="sell-cells"]'), sells, 'SELL');
    if (metaEl) metaEl.textContent = `买 ${buys.length} · 卖 ${sells.length}`;
  };

  const updateHeroSummary = (payloads) => {
    const liveStatus = document.getElementById('live-status-pill');
    if (liveStatus) {
      liveStatus.textContent = '● 三标的已同步';
      liveStatus.style.color = '#389e0d';
      liveStatus.style.borderColor = '#b7eb8f';
      liveStatus.style.background = '#f6ffed';
    }

    const latest = payloads
      .map(p => p?.data_as_of)
      .filter(Boolean)
      .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
    const pillAsOf = document.getElementById('pill-as-of');
    if (pillAsOf && latest) pillAsOf.textContent = `数据时点：${formatTime(latest)}`;

    const pillMode = document.getElementById('pill-mode');
    if (pillMode) pillMode.textContent = '模式：三标的看板';

    const buyTotal = payloads.reduce((sum, p) => sum + normalizeTimeline(p).buys.length, 0);
    const sellTotal = payloads.reduce((sum, p) => sum + normalizeTimeline(p).sells.length, 0);
    const elLit = document.getElementById('stat-lit-count');
    if (elLit) elLit.textContent = `买 ${buyTotal} / 卖 ${sellTotal}`;
    const elCurrent = document.getElementById('stat-current-code');
    if (elCurrent) elCurrent.textContent = '三标的';
    const elStopped = document.getElementById('stat-stopped-code');
    if (elStopped) elStopped.textContent = buyTotal > 0 ? '多头推进中' : '观察中';
    const elStar = document.getElementById('stat-star-code');
    if (elStar) elStar.textContent = sellTotal > 0 ? `${sellTotal} 个卖点` : '未触发';
    const elStarTime = document.getElementById('stat-star-time');
    if (elStarTime) elStarTime.textContent = sellTotal > 0 ? '见各标的空方轨' : '卖出信号暂无';
    const elSummary = document.getElementById('stat-sell-summary');
    if (elSummary) elSummary.textContent = `${sellTotal} 个`;
  };

  const updateQuoteUI = (symbol, payload) => {
    const item = findQuoteItem(payload, symbol) || payload?.items?.[0] || null;
    const badge = document.querySelector(`.instrument-board[data-symbol="${symbol}"] [data-role="quote"]`);
    const pill = document.getElementById('pill-stock-quote');
    if (!item || typeof item.price !== 'number') {
      if (badge) badge.textContent = '行情暂不可用';
      return;
    }
    const changePct = typeof item.change_percent === 'number' ? item.change_percent : item.change_pct;
    const isUp = changePct > 0;
    const isDown = changePct < 0;
    const color = isUp ? '#cf1322' : isDown ? '#389e0d' : '#475569';
    const sign = isUp ? '+' : '';
    const text = `¥${item.price.toFixed(2)} ${sign}${typeof changePct === 'number' ? changePct.toFixed(2) : '0.00'}%`;
    if (badge) {
      badge.textContent = text;
      badge.style.color = color;
    }
    // Hero pill shows the first instrument quote as a compact summary.
    if (pill && symbol === INSTRUMENTS[0].symbol) {
      pill.textContent = `实时：${INSTRUMENTS[0].name} ${text}`;
      pill.style.color = color;
      pill.style.background = isUp ? '#fff1f0' : isDown ? '#f6ffed' : '#f1f5f9';
      pill.style.borderColor = isUp ? '#ffa39e' : isDown ? '#b7eb8f' : '#cbd5e1';
    }
  };

  const fetchOneSignals = async (symbol) => {
    const res = await fetch(`/api/public/v1/rolling-signals?symbol=${encodeURIComponent(symbol)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  };

  const fetchOneQuote = async (meta) => {
    const res = await fetch(`/api/public/v1/quote?symbol=${encodeURIComponent(meta.symbol)}&exchange=${encodeURIComponent(meta.exchange)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return normalizeQuotePayload(await res.json());
  };

  let signalInFlight = false;
  const fetchAllSignals = async () => {
    if (signalInFlight || document.hidden) return;
    signalInFlight = true;
    try {
      const payloads = await Promise.all(INSTRUMENTS.map(async (meta) => {
        try {
          const data = await fetchOneSignals(meta.symbol);
          updateBoard(meta.symbol, data);
          return data;
        } catch {
          return null;
        }
      }));
      updateHeroSummary(payloads.filter(Boolean));
    } finally {
      signalInFlight = false;
    }
  };

  let quoteInFlight = false;
  const fetchAllQuotes = async () => {
    if (quoteInFlight || document.hidden) return;
    quoteInFlight = true;
    try {
      await Promise.all(INSTRUMENTS.map(async (meta) => {
        try {
          const data = await fetchOneQuote(meta);
          updateQuoteUI(meta.symbol, data);
        } catch {
          updateQuoteUI(meta.symbol, null);
        }
      }));
    } finally {
      quoteInFlight = false;
    }
  };

  setTimeout(() => {
    fetchAllSignals();
    fetchAllQuotes();
  }, 400);

  if (window.EtfLivePoll?.startLivePoll) {
    window.EtfLivePoll.startLivePoll({ intervalMs: 15000, immediate: false, tick: async () => { await fetchAllQuotes(); }});
    window.EtfLivePoll.startLivePoll({ intervalMs: 30000, immediate: false, tick: async () => { await fetchAllSignals(); }});
  } else {
    setInterval(fetchAllSignals, 30000);
    setInterval(fetchAllQuotes, 15000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        void fetchAllSignals();
        void fetchAllQuotes();
      }
    });
  }
})();
