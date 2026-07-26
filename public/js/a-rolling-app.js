/**
 * A-share rolling multi-instrument board.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 *
 * Observation dots:
 *  - buy: red, lit by 1.75h / 105m
 *  - sell: green, lit by 10m
 * Formal buy cells: 2h → 5.5h (observation excluded from rail badges)
 */
(function() {
  const { normalizeQuotePayload, findQuoteItem } = window.EtfQuote || {};
  if (!normalizeQuotePayload || !findQuoteItem) {
    console.error('EtfQuote adapter missing');
    return;
  }

  const FORMAL_BUY_ORDER = ['2h', '2.5h', '3h', '3.5h', '4h', '4.5h', '5h', '5.5h'];
  const FORMAL_SELL_ORDER = ['15m', '30m', '60m', '90m', '120m', '150m', '180m', '210m', '240m'];
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

    const buys = timeline
      .filter(item => item && item.type === 'BUY' && !isBuyObservation(item.code))
      .sort((a, b) => {
        const oa = buyOrderIndex(a.code);
        const ob = buyOrderIndex(b.code);
        if (oa !== ob) return oa - ob;
        return new Date(a.triggered_at || 0).getTime() - new Date(b.triggered_at || 0).getTime();
      });

    const sells = timeline
      .filter(item => item && item.type === 'SELL' && !isSellObservation(item.code))
      .sort((a, b) => {
        const oa = sellOrderIndex(a.code);
        const ob = sellOrderIndex(b.code);
        if (oa !== ob) return oa - ob;
        return new Date(a.triggered_at || 0).getTime() - new Date(b.triggered_at || 0).getTime();
      });

    return { buys, sells, buyObservation, sellObservation };
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

    const { buys, sells, buyObservation, sellObservation } = normalizeTimeline(data);
    renderCells(
      board.querySelector('[data-role="buy-cells"]'),
      board.querySelector('[data-role="sell-cells"]'),
      buys,
      sells
    );
    if (metaEl) metaEl.textContent = `买 ${buys.length} · 卖 ${sells.length}`;

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
    if (elStopped) elStopped.textContent = (buyTotal + sellTotal) > 0 ? '信号推进中' : '观察中';
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
          paintBoard(meta.symbol, data);
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
    }
  };

  setTimeout(() => {
    fetchAllSignals();
    fetchAllQuotes();
  }, 400);

  // Custom horizontal scroll track under each instrument board.
  // Mobile browsers often hide native scrollbars; this keeps a visible slider.
  const syncScrollUi = (board) => {
    const scroller = board.querySelector('[data-role="signal-scroller"]');
    const track = board.querySelector('[data-role="scroll-track"]');
    const thumb = board.querySelector('[data-role="scroll-thumb"]');
    const range = board.querySelector('[data-role="scroll-range"]');
    if (!scroller || !track || !thumb) return;

    const maxScroll = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    if (maxScroll <= 1) {
      track.style.opacity = '0.35';
      thumb.style.width = '100%';
      thumb.style.left = '0px';
      if (range) range.textContent = '已显示全部';
      return;
    }

    track.style.opacity = '1';
    const trackWidth = track.clientWidth || 1;
    const thumbWidth = Math.max(42, Math.round((scroller.clientWidth / scroller.scrollWidth) * trackWidth));
    const maxThumbLeft = Math.max(0, trackWidth - thumbWidth);
    const left = Math.round((scroller.scrollLeft / maxScroll) * maxThumbLeft);
    thumb.style.width = `${thumbWidth}px`;
    thumb.style.left = `${left}px`;
    if (range) range.textContent = `可滑 ${maxScroll}px`;
  };

  const bindScroller = (board) => {
    const scroller = board.querySelector('[data-role="signal-scroller"]');
    const track = board.querySelector('[data-role="scroll-track"]');
    const thumb = board.querySelector('[data-role="scroll-thumb"]');
    if (!scroller || !track || !thumb || board.dataset.scrollBound === '1') return;
    board.dataset.scrollBound = '1';

    const onScroll = () => syncScrollUi(board);
    scroller.addEventListener('scroll', onScroll, { passive: true });

    let dragging = false;
    let startX = 0;
    let startLeft = 0;

    const maxThumbLeft = () => {
      const trackWidth = track.clientWidth || 1;
      const thumbWidth = thumb.offsetWidth || 42;
      return Math.max(0, trackWidth - thumbWidth);
    };

    const scrollFromThumbLeft = (thumbLeft) => {
      const maxScroll = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
      const mtl = maxThumbLeft();
      const ratio = mtl > 0 ? Math.min(1, Math.max(0, thumbLeft / mtl)) : 0;
      scroller.scrollLeft = ratio * maxScroll;
      syncScrollUi(board);
    };

    const onPointerDown = (event) => {
      dragging = true;
      startX = event.clientX;
      startLeft = thumb.offsetLeft || 0;
      thumb.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    };
    const onPointerMove = (event) => {
      if (!dragging) return;
      const next = startLeft + (event.clientX - startX);
      scrollFromThumbLeft(Math.min(maxThumbLeft(), Math.max(0, next)));
    };
    const onPointerUp = (event) => {
      if (!dragging) return;
      dragging = false;
      try { thumb.releasePointerCapture?.(event.pointerId); } catch {}
    };

    thumb.addEventListener('pointerdown', onPointerDown);
    thumb.addEventListener('pointermove', onPointerMove);
    thumb.addEventListener('pointerup', onPointerUp);
    thumb.addEventListener('pointercancel', onPointerUp);

    track.addEventListener('pointerdown', (event) => {
      if (event.target === thumb) return;
      const rect = track.getBoundingClientRect();
      const x = event.clientX - rect.left - (thumb.offsetWidth / 2);
      scrollFromThumbLeft(x);
    });

    window.addEventListener('resize', onScroll, { passive: true });
    // Wait one frame for layout after live signal updates.
    requestAnimationFrame(() => syncScrollUi(board));
  };

  const initAllScrollers = () => {
    document.querySelectorAll('.instrument-board').forEach((board) => {
      bindScroller(board);
      syncScrollUi(board);
    });
  };

  let applyBoard = updateBoard;
  const paintBoard = (symbol, data) => {
    applyBoard(symbol, data);
    const board = document.querySelector(`.instrument-board[data-symbol="${symbol}"]`);
    if (board) requestAnimationFrame(() => syncScrollUi(board));
  };

  initAllScrollers();

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
