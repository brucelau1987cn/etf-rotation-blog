/**
 * A-share rolling multi-instrument board.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 *
 * Observation dots:
 *  - buy: red, lit by 1.75h / 105m
 *  - sell: green, lit by 10m
 * Formal rails:
 *  - buy / sell each show the latest 4 formal windows only (max 8 badges)
 */
(function() {
  const { normalizeQuotePayload, findQuoteItem } = window.EtfQuote || {};
  if (!normalizeQuotePayload || !findQuoteItem) {
    console.error('EtfQuote adapter missing');
    return;
  }

  const FORMAL_BUY_ORDER = ['2h', '2.5h', '3h', '3.5h', '4h', '4.5h', '5h', '5.5h', '6h', '6.5h', '7h', '7.5h', '8h'];
  const FORMAL_SELL_ORDER = ['15m', '30m', '60m', '90m', '120m', '150m', '180m', '210m', '240m'];
  const MAX_FORMAL_PER_SIDE = 4;
  const INSTRUMENTS = [
    { name: '上海电力', exchange: 'SSE', symbol: '600021' },
    { name: '创新医疗', exchange: 'SZSE', symbol: '002173' },
    { name: '三安光电', exchange: 'SSE', symbol: '600703' },
    { name: '深科技', exchange: 'SZSE', symbol: '000021' },
    { name: '德福科技', exchange: 'SZSE', symbol: '301511' },
    { name: '民爆光电', exchange: 'SZSE', symbol: '301362' },
    { name: '海光信息', exchange: 'SSE', symbol: '688041' },
    { name: '东方明珠', exchange: 'SSE', symbol: '600637' },
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

  const buyOrderIndex = (code) => {
    const i = FORMAL_BUY_ORDER.indexOf(code);
    return i === -1 ? 999 : i;
  };

  const sellOrderIndex = (code) => {
    const i = FORMAL_SELL_ORDER.indexOf(code);
    return i === -1 ? 999 : i;
  };

  const isBuyObservation = (code) => code === '1.75h' || code === '105m';
  const isSellObservation = (code) => code === '10m';

  /** Higher cycle rank is treated as the newer display window. */
  const takeLatestFormal = (items, orderIndex, limit = MAX_FORMAL_PER_SIDE) => {
    const sorted = items.slice().sort((a, b) => {
      const oa = orderIndex(a.code);
      const ob = orderIndex(b.code);
      if (oa !== ob) return oa - ob;
      return new Date(a.triggered_at || 0).getTime() - new Date(b.triggered_at || 0).getTime();
    });
    return sorted.slice(Math.max(0, sorted.length - limit));
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

    const buyObservation = timeline.find(item => item && item.type === 'BUY' && isBuyObservation(item.code)) || null;
    const sellObservation = timeline.find(item => item && item.type === 'SELL' && isSellObservation(item.code)) || null;

    const allBuys = timeline.filter(item => item && item.type === 'BUY' && !isBuyObservation(item.code));
    const allSells = timeline.filter(item => item && item.type === 'SELL' && !isSellObservation(item.code));
    const buys = takeLatestFormal(allBuys, buyOrderIndex);
    const sells = takeLatestFormal(allSells, sellOrderIndex);

    return {
      buys,
      sells,
      buyObservation,
      sellObservation,
      buyTotal: allBuys.length,
      sellTotal: allSells.length,
    };
  };

  const setWatchDot = (el, lit, titleLit, titleOff) => {
    if (!el) return;
    el.classList.toggle('lit', !!lit);
    el.title = lit ? titleLit : titleOff;
    el.setAttribute('aria-label', lit ? titleLit : titleOff);
  };

  const renderCells = (buyContainer, sellContainer, buys, sells) => {
    if (!buyContainer || !sellContainer) return;
    buyContainer.replaceChildren();
    sellContainer.replaceChildren();

    const appendSpacer = (container, key) => {
      const spacer = document.createElement('div');
      spacer.className = 'cell-spacer';
      spacer.setAttribute('aria-hidden', 'true');
      if (key) spacer.dataset.alignKey = key;
      container.appendChild(spacer);
    };

    const appendBadge = (container, item, kind) => {
      const badge = document.createElement('div');
      badge.className = kind === 'BUY'
        ? 'cell-badge buy-buy'
        : `cell-badge sell-${(item.sell_state || 'sell').toLowerCase()}`;
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
    };

    // Sell rail is left-padded by buy count so formal sells sit after buys.
    buys.forEach((item) => appendSpacer(sellContainer, item.code));
    if (sells.length === 0) {
      if (buys.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-rail';
        empty.textContent = '卖出信号暂无';
        sellContainer.appendChild(empty);
      }
    } else {
      sells.forEach((item) => appendBadge(sellContainer, item, 'SELL'));
    }

    if (buys.length === 0) {
      if (sells.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-rail';
        empty.textContent = '买入信号暂无';
        buyContainer.appendChild(empty);
      } else {
        sells.forEach((item) => appendSpacer(buyContainer, item.code));
      }
    } else {
      buys.forEach((item) => appendBadge(buyContainer, item, 'BUY'));
      sells.forEach((item) => appendSpacer(buyContainer, item.code));
    }
  };

  const updateBoard = (symbol, data) => {
    const board = document.querySelector(`.instrument-board[data-symbol="${symbol}"]`);
    if (!board || !data) return;
    const nameEl = board.querySelector('[data-role="inst-name"]');
    const symbolEl = board.querySelector('[data-role="inst-symbol"]');
    const metaEl = board.querySelector('[data-role="signal-meta"]');
    if (nameEl && data.instrument?.instrument_name) nameEl.textContent = data.instrument.instrument_name;
    if (symbolEl && data.instrument?.symbol) symbolEl.textContent = data.instrument.symbol;

    const { buys, sells, buyObservation, sellObservation, buyTotal, sellTotal } = normalizeTimeline(data);
    renderCells(
      board.querySelector('[data-role="buy-cells"]'),
      board.querySelector('[data-role="sell-cells"]'),
      buys,
      sells
    );
    if (metaEl) metaEl.textContent = `买 ${buyTotal} · 卖 ${sellTotal}`;

    setWatchDot(
      board.querySelector('[data-role="buy-watch-dot"]'),
      !!buyObservation,
      `1.75h 观察已触发 ${formatTime(buyObservation?.triggered_at, true, false)}`,
      '1.75h 观察窗口未触发'
    );
    setWatchDot(
      board.querySelector('[data-role="sell-watch-dot"]'),
      !!sellObservation,
      `10m 观察已触发 ${formatTime(sellObservation?.triggered_at, true, false)}`,
      '10m 观察窗口未触发'
    );
  };

  const updateHeroSummary = (payloads) => {
    const liveStatus = document.getElementById('live-status-pill');
    if (liveStatus) {
      liveStatus.textContent = `● ${INSTRUMENTS.length} 标的已同步`;
      liveStatus.style.color = '#389e0d';
      liveStatus.style.borderColor = '#b7eb8f';
      liveStatus.style.background = '#f6ffed';
    }

    const latest = payloads
      .map(p => p?.data_as_of)
      .filter(Boolean)
      .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
    const pillAsOf = document.getElementById('pill-as-of');
    if (pillAsOf && latest) pillAsOf.textContent = `信号时间：${formatTime(latest)}`;

    const rows = INSTRUMENTS.map((meta) => {
      const payload = payloads.find(p => p?.instrument?.symbol === meta.symbol) || null;
      const split = normalizeTimeline(payload || {});
      return {
        name: payload?.instrument?.instrument_name || meta.name,
        symbol: meta.symbol,
        buyCount: split.buys.length,
        sellCount: split.sells.length,
        buyWatch: !!split.buyObservation,
        sellWatch: !!split.sellObservation,
        latest: [...split.buys, ...split.sells]
          .filter(item => item?.triggered_at)
          .sort((a, b) => new Date(b.triggered_at || 0).getTime() - new Date(a.triggered_at || 0).getTime())[0] || null,
      };
    });

    const buyTotal = rows.reduce((sum, row) => sum + row.buyCount, 0);
    const sellTotal = rows.reduce((sum, row) => sum + row.sellCount, 0);
    const buyWatchLit = rows.filter(row => row.buyWatch).length;
    const sellWatchLit = rows.filter(row => row.sellWatch).length;
    const latestEvent = rows
      .map(row => row.latest && ({
        name: row.name,
        type: row.latest.type,
        code: row.latest.code,
        at: row.latest.triggered_at,
      }))
      .filter(Boolean)
      .sort((a, b) => new Date(b.at || 0).getTime() - new Date(a.at || 0).getTime())[0] || null;

    const setText = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    setText('stat-buy-total', String(buyTotal));
    setText('stat-sell-total', String(sellTotal));
    setText('stat-buy-breakdown', rows.map(row => `${row.name}${row.buyCount}`).join(' · ') || '—');
    setText('stat-sell-breakdown', rows.map(row => `${row.name}${row.sellCount}`).join(' · ') || '—');
    setText('stat-buy-watch', String(buyWatchLit));
    setText('stat-sell-watch', String(sellWatchLit));
    setText(
      'stat-latest-action',
      latestEvent
        ? `最新 ${latestEvent.name} ${latestEvent.type === 'BUY' ? '买入' : '卖出'} ${latestEvent.code} · ${formatTime(latestEvent.at)}`
        : '等待信号'
    );
  };

  const updateQuoteUI = (symbol, payload) => {
    const item = findQuoteItem(payload, symbol) || payload?.items?.[0] || null;
    const badge = document.querySelector(`.instrument-board[data-symbol="${symbol}"] [data-role="quote"]`);
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
  };

  const QUOTE_INTERVAL_MS = 15000;
  const SIGNAL_INTERVAL_MS = 30000;
  let nextQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
  let countdownTimer = null;

  const paintCountdown = () => {
    const pill = document.getElementById('pill-refresh-countdown');
    if (!pill) return;
    if (document.hidden) {
      pill.textContent = '实时：页面后台暂停';
      pill.style.color = '#64748b';
      pill.style.background = '#f1f5f9';
      pill.style.borderColor = '#cbd5e1';
      return;
    }
    const remainMs = Math.max(0, nextQuoteAt - Date.now());
    const remainSec = Math.ceil(remainMs / 1000);
    if (remainSec <= 0) {
      pill.textContent = '实时：刷新中…';
      pill.style.color = '#0958d9';
      pill.style.background = '#e6f4ff';
      pill.style.borderColor = '#91caff';
      return;
    }
    pill.textContent = `实时：${remainSec}s 后刷新`;
    pill.style.color = '#334155';
    pill.style.background = '#f1f5f9';
    pill.style.borderColor = '#cbd5e1';
  };

  const armQuoteCountdown = () => {
    nextQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
    paintCountdown();
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
          updateQuoteUI(meta.symbol, await fetchOneQuote(meta));
        } catch {
          updateQuoteUI(meta.symbol, null);
        }
      }));
    } finally {
      quoteInFlight = false;
      armQuoteCountdown();
    }
  };

  setTimeout(() => {
    fetchAllSignals();
    fetchAllQuotes();
  }, 400);

  countdownTimer = window.setInterval(paintCountdown, 250);
  paintCountdown();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      armQuoteCountdown();
      void fetchAllSignals();
      void fetchAllQuotes();
    } else {
      paintCountdown();
    }
  });

  if (window.EtfLivePoll?.startLivePoll) {
    window.EtfLivePoll.startLivePoll({ intervalMs: QUOTE_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllQuotes(); }});
    window.EtfLivePoll.startLivePoll({ intervalMs: SIGNAL_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllSignals(); }});
  } else {
    setInterval(fetchAllSignals, SIGNAL_INTERVAL_MS);
    setInterval(fetchAllQuotes, QUOTE_INTERVAL_MS);
  }
})();
