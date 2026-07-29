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
  const A_INSTRUMENTS = [
    { name: '上海电力', exchange: 'SSE', symbol: '600021' },
    { name: '创新医疗', exchange: 'SZSE', symbol: '002173' },
    { name: '三安光电', exchange: 'SSE', symbol: '600703' },
    { name: '深科技', exchange: 'SZSE', symbol: '000021' },
    { name: '德福科技', exchange: 'SZSE', symbol: '301511' },
    { name: '民爆光电', exchange: 'SZSE', symbol: '301362' },
    { name: '海光信息', exchange: 'SSE', symbol: '688041' },
    { name: '东方明珠', exchange: 'SSE', symbol: '600637' },
    { name: '长鑫科技', exchange: 'SSE', symbol: '688825' },
    { name: '国民技术', exchange: 'SZSE', symbol: '300077' },
    { name: '澜起科技', exchange: 'SSE', symbol: '688008' },
    { name: '华天科技', exchange: 'SZSE', symbol: '002185' },
  ];
  const HK_INSTRUMENTS = [
    { name: '中国宏桥', exchange: 'HKEX', symbol: '01378' },
  ];
  const US_INSTRUMENTS = [
    { name: '特斯拉', exchange: 'NASDAQ', symbol: 'TSLA' },
  ];
  // Futures instruments are added only when explicitly named by the user.
  const FUTURES_INSTRUMENTS = [];
  const INDEX_SETS = {
    a: [
      { name: '上证指数', symbol: '000001', querySymbol: '000001.SH', continuous: false },
      { name: '深证成指', symbol: '399001', querySymbol: '399001.SZ', continuous: false },
      { name: '创业板指', symbol: '399006', querySymbol: '399006.SZ', continuous: false },
      // 24H continuous quotes under the A-share index card (独立刷新，不绑 A 股交易时段).
      { name: '现货黄金', symbol: 'hf_XAU', querySymbol: 'hf_XAU', continuous: true },
      { name: '现货白银', symbol: 'hf_XAG', querySymbol: 'hf_XAG', continuous: true },
      { name: '国际原油', symbol: 'hf_CL', querySymbol: 'hf_CL', continuous: true },
      { name: '美元指数', symbol: 'DINIW', querySymbol: 'DINIW', continuous: true },
    ],
    futures: [
      { name: '黄金连续', symbol: 'AU0', querySymbol: 'nf_AU0', continuous: false },
      { name: '原油连续', symbol: 'SC0', querySymbol: 'nf_SC0', continuous: false },
      { name: '豆粕连续', symbol: 'M0', querySymbol: 'nf_M0', continuous: false },
    ],
    hk: [
      { name: '恒生指数', symbol: 'HSI', querySymbol: 'HSI.HK', continuous: false },
      { name: '恒生综合指数', symbol: 'HSCI', querySymbol: 'HSCI.HK', continuous: false },
      { name: '恒生科技指数', symbol: 'HSTECH', querySymbol: 'HSTECH.HK', continuous: false },
    ],
    us: [
      { name: '标普500指数', symbol: 'INX', querySymbol: 'INX.US', continuous: false },
      { name: '纳斯达克综合指数', symbol: 'IXIC', querySymbol: 'IXIC.US', continuous: false },
      { name: '道琼斯工业平均指数', symbol: 'DJI', querySymbol: 'DJI.US', continuous: false },
    ],
  };
  const currentScript = document.currentScript;
  const market = ['a', 'futures', 'hk', 'us'].includes(currentScript?.dataset?.market) ? currentScript.dataset.market : 'a';
  // Futures has day/night sessions per product; use free-running poll (no CN_A/HK/US gate).
  const calendarMarket = market === 'us' ? 'US' : market === 'hk' ? 'HK' : market === 'futures' ? null : 'CN_A';
  const INSTRUMENTS = market === 'us'
    ? US_INSTRUMENTS
    : market === 'hk'
      ? HK_INSTRUMENTS
      : market === 'futures'
        ? FUTURES_INSTRUMENTS
        : A_INSTRUMENTS;

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
        label: c.label,
        price: Number.isFinite(Number(c.price ?? c.trigger_price)) ? Number(c.price ?? c.trigger_price) : null,
        price_source: c.price_source || c.trigger_price_source || null,
      }));
      const sellItems = (data?.sell_chain?.nodes || []).map(n => ({
        type: 'SELL',
        code: n.code,
        triggered_at: n.triggered_at,
        label: n.label,
        price: Number.isFinite(Number(n.price ?? n.trigger_price)) ? Number(n.price ?? n.trigger_price) : null,
        price_source: n.price_source || n.trigger_price_source || null,
      }));
      timeline = [...buyItems, ...sellItems];
    } else {
      timeline = timeline.map((item) => ({
        ...item,
        price: Number.isFinite(Number(item?.price ?? item?.trigger_price))
          ? Number(item.price ?? item.trigger_price)
          : null,
        price_source: item?.price_source || item?.trigger_price_source || null,
      }));
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
      allBuys,
      allSells,
      buyObservation,
      sellObservation,
      buyTotal: allBuys.length,
      sellTotal: allSells.length,
    };
  };

  const formatBadgePrice = (value) => {
    const price = Number(value);
    if (!Number.isFinite(price) || price <= 0) return '—';
    return `¥${price.toFixed(2)}`;
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

      // 3-line formal badge: cycle → signal-point price → time
      const codeEl = document.createElement('div');
      codeEl.className = 'badge-code';
      codeEl.textContent = item.code;

      const priceEl = document.createElement('div');
      priceEl.className = 'badge-price';
      priceEl.textContent = formatBadgePrice(item.price);
      priceEl.title = Number.isFinite(Number(item.price)) && Number(item.price) > 0
        ? `信号点股价 ${formatBadgePrice(item.price)}`
        : '信号点股价未入库';

      const timeEl = document.createElement('div');
      timeEl.className = 'badge-time';
      timeEl.textContent = formatTime(item.triggered_at, true, false);

      badge.append(codeEl, priceEl, timeEl);
      container.appendChild(badge);
      if (kind === 'SELL' && item.code === '240m') {
        const stopCard = document.createElement('div');
        stopCard.className = 'stop-validation-card';
        stopCard.dataset.role = 'stop-validation';
        stopCard.setAttribute('aria-label', '空方力量达到240分钟，停止验证');
        const title = document.createElement('strong');
        title.textContent = '停止验证';
        const note = document.createElement('span');
        note.textContent = '空方已达 240m';
        stopCard.append(title, note);
        container.appendChild(stopCard);
      }
    };

    // Sell rail is left-padded by buy count so formal sells sit after buys.
    buys.forEach((item) => appendSpacer(sellContainer, item.code));
    if (sells.length === 0) {
      if (buys.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-rail';
        empty.textContent = '空方信号暂无';
        sellContainer.appendChild(empty);
      }
    } else {
      sells.forEach((item) => appendBadge(sellContainer, item, 'SELL'));
    }

    if (buys.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-rail';
      empty.textContent = '多方信号暂无';
      buyContainer.appendChild(empty);
      sells.forEach((item) => {
        appendSpacer(buyContainer, item.code);
        if (item.code === '240m') appendSpacer(buyContainer, 'stop-validation');
      });
    } else {
      buys.forEach((item) => appendBadge(buyContainer, item, 'BUY'));
      sells.forEach((item) => {
        appendSpacer(buyContainer, item.code);
        if (item.code === '240m') appendSpacer(buyContainer, 'stop-validation');
      });
    }
  };

  const formatStartDate = (value) => {
    if (!value) return '—';
    const raw = String(value).trim();
    const m = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (m) return `${Number(m[1])}/${Number(m[2])}/${Number(m[3])}`;
    const date = new Date(raw);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleDateString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
      }).replace(/年|月/g, '/').replace(/日/g, '');
    }
    return raw;
  };

  const updateBoard = (symbol, data) => {
    const board = document.querySelector(`.instrument-board[data-symbol="${symbol}"]`);
    if (!board || !data) return;
    const nameEl = board.querySelector('[data-role="inst-name"]');
    const symbolEl = board.querySelector('[data-role="inst-symbol"]');
    const metaEl = board.querySelector('[data-role="signal-meta"]');
    const startDateEl = board.querySelector('[data-role="start-date"]');
    if (nameEl && data.instrument?.instrument_name) nameEl.textContent = data.instrument.instrument_name;
    if (symbolEl && data.instrument?.symbol) symbolEl.textContent = data.instrument.symbol;

    const { buys, sells, buyObservation, sellObservation } = normalizeTimeline(data);
    renderCells(
      board.querySelector('[data-role="buy-cells"]'),
      board.querySelector('[data-role="sell-cells"]'),
      buys,
      sells
    );
    const startDate = data.transmission?.start_date || board.getAttribute('data-start-date') || '';
    if (startDate) board.setAttribute('data-start-date', startDate);
    if (startDateEl) startDateEl.textContent = formatStartDate(startDate);
    if (metaEl && !startDateEl) {
      metaEl.replaceChildren();
      const label = document.createElement('span');
      label.className = 'start-date-label';
      label.textContent = '起始日期';
      const value = document.createElement('strong');
      value.className = 'start-date-value';
      value.setAttribute('data-role', 'start-date');
      value.textContent = formatStartDate(startDate);
      metaEl.append(label, value);
    }

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

  const summaryTickerTimers = new Map();
  const SUMMARY_ROW_HEIGHT = 44;
  const SUMMARY_VISIBLE_ROWS = 5;

  // Frontend only reads D1-persisted timeline.price. No browser kline fan-out.
  const formatTriggerPrice = (price) => {
    if (!Number.isFinite(Number(price))) return '—';
    return `¥${Number(price).toFixed(2)}`;
  };

  const clearSummaryTicker = (track) => {
    const old = summaryTickerTimers.get(track.id);
    if (!old) return;
    if (old.timer) clearInterval(old.timer);
    if (old.resetTimer) clearTimeout(old.resetTimer);
    summaryTickerTimers.delete(track.id);
  };

  // Seamless loop: always keep the viewport filled with real rows (no blank tail).
  const startSummaryTicker = (track) => {
    clearSummaryTicker(track);
    track.style.transition = '';
    track.style.transform = 'translateY(0)';
    track.querySelectorAll('[data-ticker-clone="1"]').forEach((node) => node.remove());

    const items = Array.from(track.children).filter((el) => el.classList.contains('summary-signal-item'));
    if (items.length <= SUMMARY_VISIBLE_ROWS || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }

    // Duplicate the full list once so the next page is already filled while scrolling.
    items.forEach((item) => {
      const clone = item.cloneNode(true);
      clone.dataset.tickerClone = '1';
      clone.setAttribute('aria-hidden', 'true');
      track.appendChild(clone);
    });

    let offset = 0;
    let animating = false;
    const originalCount = items.length;
    const timer = setInterval(() => {
      if (animating || document.hidden || track.closest('.signal-card')?.matches(':hover')) return;
      animating = true;
      offset += 1;
      track.style.transition = 'transform 360ms cubic-bezier(.22,.61,.36,1)';
      track.style.transform = `translateY(${-offset * SUMMARY_ROW_HEIGHT}px)`;

      const resetTimer = setTimeout(() => {
        if (offset >= originalCount) {
          track.style.transition = 'none';
          track.style.transform = 'translateY(0)';
          // Force reflow so the next animated step starts cleanly from 0.
          void track.offsetHeight;
          track.style.transition = '';
          offset = 0;
        }
        animating = false;
      }, 400);
      const state = summaryTickerTimers.get(track.id) || {};
      if (state.resetTimer) clearTimeout(state.resetTimer);
      summaryTickerTimers.set(track.id, { timer, resetTimer });
    }, 2800);
    summaryTickerTimers.set(track.id, { timer, resetTimer: null });
  };

  const renderSummarySignals = (type, signals) => {
    const track = document.getElementById(type === 'BUY' ? 'buy-signal-track' : 'sell-signal-track');
    if (!track) return;
    clearSummaryTicker(track);
    track.replaceChildren();
    if (!signals.length) {
      const empty = document.createElement('div');
      empty.className = 'summary-signal-empty';
      empty.textContent = type === 'BUY' ? '当日暂无多方信号' : '当日暂无空方信号';
      track.appendChild(empty);
      return;
    }
    signals.forEach((signal) => {
      const row = document.createElement('div');
      row.className = 'summary-signal-item';

      const identity = document.createElement('div');
      identity.className = 'summary-signal-identity';

      const title = document.createElement('div');
      title.className = 'summary-signal-title';
      const label = document.createElement('span');
      label.className = 'summary-signal-label-text';
      label.textContent = signal.name;
      title.appendChild(label);
      if (Number(signal.count) > 1) {
        const count = document.createElement('em');
        count.className = 'summary-signal-count';
        count.textContent = String(signal.count);
        count.title = `当日累计 ${signal.count} 次`;
        title.appendChild(count);
      }

      const meta = document.createElement('div');
      meta.className = 'summary-signal-meta';
      if (signal.symbol) {
        const symbol = document.createElement('span');
        symbol.className = 'summary-signal-symbol';
        symbol.textContent = signal.symbol;
        meta.appendChild(symbol);
      }
      identity.append(title, meta);

      const point = document.createElement('strong');
      point.className = 'summary-signal-point';
      if (type === 'SELL' && signal.code === '240m') {
        point.classList.add('is-stop-validation');
        point.textContent = '停止验证 240m';
        point.title = '空方力量达到240分钟，停止验证';
      } else {
        point.textContent = signal.code;
      }

      const price = document.createElement('span');
      price.className = 'summary-signal-price';
      price.dataset.role = 'trigger-price';
      if (Number.isFinite(Number(signal.price))) {
        price.textContent = formatTriggerPrice(signal.price);
        price.title = `信号点 ${formatTime(signal.at, true, false)} · ${formatTriggerPrice(signal.price)}`;
      } else {
        // Missing D1 price: show dash instead of spamming kline on every render.
        price.textContent = '—';
        price.title = '信号点股价未入库';
      }

      const time = document.createElement('time');
      time.className = 'summary-signal-time';
      // Keep time-only on the tape; full stamp stays in title for hover/long-press.
      time.textContent = formatTime(signal.at, false, false);
      time.dateTime = signal.at || '';
      time.title = formatTime(signal.at, true, false);

      // Right-side tape: period + trigger price + clock.
      const tape = document.createElement('div');
      tape.className = 'summary-signal-tape';
      tape.append(point, price, time);

      row.append(identity, tape);
      track.appendChild(row);
    });
    startSummaryTicker(track);
  };

  const isTodayShanghai = (value) => {
    if (!value) return false;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return false;
    const key = (target) => new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(target);
    return key(date) === key(new Date());
  };

  const shanghaiTodayLabel = () => {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const month = parts.find((part) => part.type === 'month')?.value || '01';
    const day = parts.find((part) => part.type === 'day')?.value || '01';
    return `${month}/${day}`;
  };

  const renderTodayCount = (type, count) => {
    const el = document.getElementById(type === 'BUY' ? 'buy-today-count' : 'sell-today-count');
    if (!el) return;
    el.textContent = `${shanghaiTodayLabel()} · ${count}个信号（含观察）`;
  };

  const updateHeroSummary = (payloads) => {
    const liveStatus = document.getElementById('live-status-pill');
    if (liveStatus) {
      if (!INSTRUMENTS.length) {
        liveStatus.textContent = '等待点名标的';
        liveStatus.style.color = '#64748b';
        liveStatus.style.borderColor = '#cbd5e1';
        liveStatus.style.background = '#f8fafc';
      } else {
        const delivery = window.ARollingDelivery?.summarize(payloads, INSTRUMENTS.length) || {
          state: 'lkg',
          text: `⚠ 0/${INSTRUMENTS.length} 实时 · 静态快照`,
        };
        liveStatus.textContent = delivery.text;
        const isLive = delivery.state === 'live';
        liveStatus.style.color = isLive ? '#389e0d' : '#ad6800';
        liveStatus.style.borderColor = isLive ? '#b7eb8f' : '#ffd591';
        liveStatus.style.background = isLive ? '#f6ffed' : '#fff7e6';
      }
    }
    if (!payloads.length) {
      renderSummarySignals('BUY', []);
      renderSummarySignals('SELL', []);
      renderTodayCount('BUY', 0);
      renderTodayCount('SELL', 0);
      return;
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
        buyCount: split.buyTotal,
        sellCount: split.sellTotal,
        buyWatch: !!split.buyObservation,
        sellWatch: !!split.sellObservation,
      };
    });

    // Main list: one row per stock (latest signal), with cumulative count after name.
    const todaySignalsFor = (type) => {
      const bySymbol = new Map();
      rows.forEach((row) => {
        const payload = payloads.find(p => p?.instrument?.symbol === row.symbol) || null;
        const split = normalizeTimeline(payload || {});
        const formal = type === 'BUY' ? split.allBuys : split.allSells;
        const observation = type === 'BUY' ? split.buyObservation : split.sellObservation;
        const items = (observation ? [...formal, observation] : formal)
          .filter(item => isTodayShanghai(item?.triggered_at))
          .sort((a, b) => new Date(b.triggered_at || 0).getTime() - new Date(a.triggered_at || 0).getTime());
        if (!items.length) return;
        const latest = items[0];
        bySymbol.set(row.symbol, {
          name: row.name,
          symbol: row.symbol,
          code: latest.code,
          at: latest.triggered_at,
          count: items.length,
          // Prefer D1-persisted signal-point price; avoid frontend kline fan-out.
          price: Number.isFinite(Number(latest.price)) ? Number(latest.price) : null,
          price_source: latest.price_source || null,
        });
      });
      return [...bySymbol.values()]
        .sort((a, b) => new Date(b.at || 0).getTime() - new Date(a.at || 0).getTime());
    };

    const todayBuys = todaySignalsFor('BUY');
    const todaySells = todaySignalsFor('SELL');
    // Header count still includes every formal + observation hit today.
    const todayHitCount = (type) => rows.reduce((sum, row) => {
      const payload = payloads.find(p => p?.instrument?.symbol === row.symbol) || null;
      const split = normalizeTimeline(payload || {});
      const formal = type === 'BUY' ? split.allBuys : split.allSells;
      const observation = type === 'BUY' ? split.buyObservation : split.sellObservation;
      const items = observation ? [...formal, observation] : formal;
      return sum + items.filter(item => isTodayShanghai(item?.triggered_at)).length;
    }, 0);
    renderSummarySignals('BUY', todayBuys);
    renderSummarySignals('SELL', todaySells);
    renderTodayCount('BUY', todayHitCount('BUY'));
    renderTodayCount('SELL', todayHitCount('SELL'));
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
    const isHk = String(item.market || item.exchange || '').toUpperCase().includes('HK')
      || String(item.market || '').toUpperCase().includes('HONG KONG');
    const isFutures = market === 'futures'
      || String(item.type || '').toLowerCase() === 'futures'
      || /^nf_/i.test(String(item.symbol || ''))
      || /^hf_/i.test(String(item.symbol || ''));
    const isUs = !isFutures && (
      /^[A-Za-z]/.test(String(symbol || ''))
      || String(item.market || item.exchange || '').toUpperCase().includes('US')
      || String(item.market || '').toUpperCase().includes('NASDAQ')
    );
    const currency = isUs ? '$' : isHk ? 'HK$' : isFutures ? '' : '¥';
    const priceText = Number.isFinite(item.price)
      ? (isFutures ? item.price.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : item.price.toFixed(2))
      : '—';
    const text = `${currency}${priceText} ${sign}${typeof changePct === 'number' ? changePct.toFixed(2) : '0.00'}%`;
    if (badge) {
      badge.textContent = text;
      badge.style.color = color;
    }
  };

  const updateMarketIndexUI = (meta, item) => {
    const card = document.querySelector(`.market-index-row[data-index-symbol="${meta.symbol}"]`);
    if (!card) return;
    const priceEl = card.querySelector('[data-role="index-price"]');
    const changeEl = card.querySelector('[data-role="index-change"]');
    const price = Number(item?.price);
    const changeAmount = Number(item?.change_amount);
    const changePct = Number(item?.change_percent ?? item?.change_pct);
    if (!Number.isFinite(price)) {
      if (priceEl) priceEl.textContent = '—';
      if (changeEl) {
        changeEl.textContent = '行情暂不可用';
        changeEl.className = 'market-index-change flat';
      }
      return;
    }
    const direction = changePct > 0 ? 'up' : changePct < 0 ? 'down' : 'flat';
    const sign = changePct > 0 ? '+' : '';
    const isContinuousQuote = !!meta.continuous
      || meta.symbol === 'hf_XAU' || meta.symbol === 'hf_XAG' || meta.symbol === 'hf_CL' || meta.symbol === 'DINIW'
      || /^hf_/i.test(String(meta.querySymbol || meta.symbol || ''));
    const digits = meta.symbol === 'DINIW' ? 4 : 2;
    if (priceEl) priceEl.textContent = price.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    if (changeEl) {
      if (isContinuousQuote) {
        // 24H continuous quotes: pct only (cleaner under index card).
        const pctText = Number.isFinite(changePct) ? `${sign}${changePct.toFixed(2)}%` : '—';
        changeEl.textContent = pctText;
      } else {
        const amountText = Number.isFinite(changeAmount) ? `${changeAmount > 0 ? '+' : ''}${changeAmount.toFixed(2)}` : '—';
        const pctText = Number.isFinite(changePct) ? `${sign}${changePct.toFixed(2)}%` : '—';
        changeEl.textContent = `${amountText} · ${pctText}`;
      }
      changeEl.className = `market-index-change ${direction}`;
    }
  };

  const resolveIndexQuoteItem = (payload, meta) => (
    findQuoteItem(payload, meta.symbol)
      || findQuoteItem(payload, meta.querySymbol)
      || payload?.quotes?.[meta.symbol]
      || payload?.quotes?.[meta.querySymbol]
      || null
  );

  const fetchIndexQuoteBatch = async (indexSymbols) => {
    if (!indexSymbols.length) return;
    try {
      const symbols = indexSymbols.map(item => item.querySymbol).join(',');
      const res = await fetch(`/api/public/v1/quote?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const payload = normalizeQuotePayload(await res.json());
      indexSymbols.forEach(meta => updateMarketIndexUI(meta, resolveIndexQuoteItem(payload, meta)));
    } catch {
      indexSymbols.forEach(meta => updateMarketIndexUI(meta, null));
    }
  };

  // Session-gated equity indices (A/HK/US) + free-running 24H continuous quotes.
  const fetchMarketIndices = async ({ continuousOnly = false, sessionOnly = false } = {}) => {
    const indexSymbols = INDEX_SETS[market] || INDEX_SETS.a;
    const batch = indexSymbols.filter((item) => {
      if (continuousOnly) return !!item.continuous;
      if (sessionOnly) return !item.continuous;
      return true;
    });
    await fetchIndexQuoteBatch(batch);
  };

  const QUOTE_INTERVAL_MS = 15000;
  // Day-locked signal board: poll gently. Same node never updates again today.
  const SIGNAL_INTERVAL_MS = 120000;
  let nextQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
  let countdownTimer = null;
  let signalFingerprint = '';
  let signalStablePolls = 0;
  let signalPollingStopped = false;

  const paintCountdown = () => {
    const pill = document.getElementById('pill-refresh-countdown');
    const indexPill = document.getElementById('index-refresh-countdown');
    if (document.hidden) {
      if (pill) {
        pill.textContent = '实时：页面后台暂停';
        pill.style.color = '#64748b';
        pill.style.background = '#f1f5f9';
        pill.style.borderColor = '#cbd5e1';
      }
      if (indexPill) {
        indexPill.textContent = '实时：页面后台暂停';
        indexPill.style.color = '#64748b';
        indexPill.style.background = '#f1f5f9';
        indexPill.style.borderColor = '#cbd5e1';
      }
      return;
    }
    const remainMs = Math.max(0, nextQuoteAt - Date.now());
    const remainSec = Math.ceil(remainMs / 1000);
    if (remainSec <= 0) {
      if (pill) {
        pill.textContent = '实时：刷新中…';
        pill.style.color = '#0958d9';
        pill.style.background = '#e6f4ff';
        pill.style.borderColor = '#91caff';
      }
      if (indexPill) {
        indexPill.textContent = '实时：刷新中…';
        indexPill.style.color = '#0958d9';
        indexPill.style.background = '#e6f4ff';
        indexPill.style.borderColor = '#91caff';
      }
      return;
    }
    if (pill) {
      pill.textContent = `实时：${remainSec}s 后刷新`;
      pill.style.color = '#334155';
      pill.style.background = '#f1f5f9';
      pill.style.borderColor = '#cbd5e1';
    }
    if (indexPill) {
      indexPill.textContent = `实时：${remainSec}s 后刷新`;
      indexPill.style.color = '#334155';
      indexPill.style.background = '#f1f5f9';
      indexPill.style.borderColor = '#cbd5e1';
    }
  };

  const armQuoteCountdown = () => {
    nextQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
    paintCountdown();
  };

  countdownTimer = window.setInterval(paintCountdown, 1000);
  paintCountdown();
  document.addEventListener('visibilitychange', paintCountdown);

  const fetchOneSignals = async (symbol) => {
    const res = await fetch(`/api/public/v1/rolling-signals?symbol=${encodeURIComponent(symbol)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  };

  const fetchOneQuote = async (meta) => {
    let symbolParam;
    if (meta.exchange === 'HKEX') {
      symbolParam = `${meta.symbol}.HK`;
    } else if (market === 'futures' || meta.exchange === 'FUTURES') {
      symbolParam = meta.querySymbol || (String(meta.symbol || '').startsWith('nf_') ? meta.symbol : `nf_${meta.symbol}`);
    } else if (/^[A-Za-z]/.test(meta.symbol)) {
      symbolParam = `${meta.symbol}.US`;
    } else {
      symbolParam = meta.symbol;
    }
    const res = await fetch(`/api/public/v1/quote?symbol=${encodeURIComponent(symbolParam)}&exchange=${encodeURIComponent(meta.exchange)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return normalizeQuotePayload(await res.json());
  };

  let signalInFlight = false;
  const fetchAllSignals = async () => {
    if (signalInFlight || document.hidden || signalPollingStopped) return;
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
      const valid = payloads.filter(Boolean);
      updateHeroSummary(valid);

      // Stop further signal polling once the day board stops changing.
      const fingerprint = valid.map(item => {
        const symbol = item?.instrument?.symbol || '';
        const events = (item?.timeline || []).map(e => `${e.type}:${e.code}:${e.triggered_at || e.received_at || ''}`).join(',');
        return `${symbol}=${events}`;
      }).join('|');
      if (fingerprint && fingerprint === signalFingerprint) {
        signalStablePolls += 1;
      } else {
        signalFingerprint = fingerprint;
        signalStablePolls = 0;
      }
      if (signalStablePolls >= 2) {
        signalPollingStopped = true;
        const pill = document.getElementById('pill-refresh-countdown');
        // Keep quote countdown running; signal board is day-locked.
        if (pill && /信号/.test(pill.textContent || '')) {
          pill.textContent = '信号：当日已锁定';
        }
      }
    } finally {
      signalInFlight = false;
    }
  };

  let quoteInFlight = false;
  const fetchAllQuotes = async () => {
    if (quoteInFlight || document.hidden) return;
    quoteInFlight = true;
    try {
      await Promise.all([
        ...INSTRUMENTS.map(async (meta) => {
          try {
            updateQuoteUI(meta.symbol, await fetchOneQuote(meta));
          } catch {
            updateQuoteUI(meta.symbol, null);
          }
        }),
        // Session-gated batch only; 24H continuous quotes have their own free-running poll.
        fetchMarketIndices({ sessionOnly: true }),
      ]);
    } finally {
      quoteInFlight = false;
      armQuoteCountdown();
    }
  };

  let continuousInFlight = false;
  let nextContinuousQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
  const paintContinuousCountdown = () => {
    const pill = document.getElementById('continuous-refresh-countdown');
    if (!pill) return;
    pill.classList.remove('is-refreshing', 'is-paused');
    if (document.hidden) {
      pill.textContent = '页面后台暂停';
      pill.classList.add('is-paused');
      return;
    }
    if (continuousInFlight) {
      pill.textContent = '刷新中…';
      pill.classList.add('is-refreshing');
      return;
    }
    const remainSec = Math.max(0, Math.ceil((nextContinuousQuoteAt - Date.now()) / 1000));
    pill.textContent = `${remainSec || 1}s 后刷新`;
  };

  const fetchContinuousQuotes = async () => {
    if (continuousInFlight || document.hidden) return;
    continuousInFlight = true;
    paintContinuousCountdown();
    try {
      await fetchMarketIndices({ continuousOnly: true });
    } finally {
      continuousInFlight = false;
      nextContinuousQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
      paintContinuousCountdown();
    }
  };
  const continuousCountdownTimer = window.setInterval(paintContinuousCountdown, 1000);
  paintContinuousCountdown();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      nextContinuousQuoteAt = 0;
      void fetchContinuousQuotes();
    }
    paintContinuousCountdown();
  });

  setTimeout(() => { fetchAllSignals(); }, 400);
  const initialQuoteLoad = setTimeout(() => {
    fetchAllQuotes();
    fetchContinuousQuotes();
  }, 120);

  if (calendarMarket && window.EtfLivePoll?.startMarketPoll) {
    window.EtfLivePoll.startMarketPoll({
      market: calendarMarket,
      intervalMs: QUOTE_INTERVAL_MS,
      immediate: true,
      tick: async () => { await fetchAllQuotes(); },
      onStatus: (text) => {
        const pill = document.getElementById('pill-refresh-countdown');
        const indexPill = document.getElementById('index-refresh-countdown');
        if (pill) pill.textContent = text;
        // Mirror the top market-status pill into the index card chip.
        if (indexPill) {
          indexPill.textContent = text;
          if (/收盘|休市|恢复|暂停/.test(String(text || ''))) {
            indexPill.style.color = '#64748b';
            indexPill.style.background = '#f1f5f9';
            indexPill.style.borderColor = '#cbd5e1';
          } else if (/刷新中/.test(String(text || ''))) {
            indexPill.style.color = '#0958d9';
            indexPill.style.background = '#e6f4ff';
            indexPill.style.borderColor = '#91caff';
          } else {
            indexPill.style.color = '#334155';
            indexPill.style.background = '#f1f5f9';
            indexPill.style.borderColor = '#cbd5e1';
          }
        }
      },
    });
    window.EtfLivePoll.startLivePoll({ intervalMs: SIGNAL_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllSignals(); }});
    // 24H continuous quotes (gold/silver/oil/DXY): free-running, independent of CN session gate.
    if (window.EtfLivePoll?.startLivePoll) {
      window.EtfLivePoll.startLivePoll({ intervalMs: QUOTE_INTERVAL_MS, immediate: false, tick: async () => { await fetchContinuousQuotes(); }});
    } else {
      setInterval(fetchContinuousQuotes, QUOTE_INTERVAL_MS);
    }
  } else if (window.EtfLivePoll?.startLivePoll) {
    // Futures (and any ungated market): free-running poll without stock session calendar.
    window.EtfLivePoll.startLivePoll({ intervalMs: QUOTE_INTERVAL_MS, immediate: true, tick: async () => {
      await Promise.all([fetchAllQuotes(), fetchContinuousQuotes()]);
    }});
    window.EtfLivePoll.startLivePoll({ intervalMs: SIGNAL_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllSignals(); }});
  } else {
    setInterval(fetchAllSignals, SIGNAL_INTERVAL_MS);
    setInterval(fetchAllQuotes, QUOTE_INTERVAL_MS);
    setInterval(fetchContinuousQuotes, QUOTE_INTERVAL_MS);
  }

  // Energy board: page by 3 instruments + code/name/initial search.
  const initBoardPager = () => {
    const list = document.getElementById('rolling-board-list');
    const pager = document.getElementById('board-pager');
    const nums = document.getElementById('board-page-nums');
    const meta = document.getElementById('board-page-meta');
    const prevBtn = document.getElementById('board-page-prev');
    const nextBtn = document.getElementById('board-page-next');
    const searchInput = document.getElementById('board-search-input');
    const empty = document.getElementById('board-search-empty');
    if (!list || !pager || !nums || !meta || !prevBtn || !nextBtn) return;

    const pageSize = Math.max(1, Number(pager.dataset.pageSize || 3));
    const boards = Array.from(list.querySelectorAll('.instrument-board'));
    if (!boards.length) return;

    let page = 1;
    let query = '';

    const normalizeQuery = (value) => String(value || '')
      .trim()
      .toLowerCase()
      .replace(/\s+/g, '');

    const filteredBoards = () => {
      if (!query) return boards;
      return boards.filter((board) => {
        const symbol = String(board.dataset.symbol || '').toLowerCase();
        const name = String(board.dataset.name || '').toLowerCase();
        const initials = String(board.dataset.initials || '').toLowerCase();
        return symbol.includes(query)
          || name.includes(query)
          || initials.includes(query)
          || initials.startsWith(query);
      });
    };

    const render = () => {
      const matched = filteredBoards();
      const totalPages = Math.max(1, Math.ceil(matched.length / pageSize));
      if (page > totalPages) page = totalPages;
      if (page < 1) page = 1;
      const start = (page - 1) * pageSize;
      const end = start + pageSize;
      const visible = new Set(matched.slice(start, end));

      boards.forEach((board) => {
        board.hidden = !visible.has(board);
      });
      if (empty) empty.hidden = matched.length > 0;

      const showPager = matched.length > pageSize;
      pager.hidden = !showPager;
      meta.textContent = matched.length
        ? `第 ${page}/${totalPages} 页 · ${matched.length} 只`
        : '无匹配';

      nums.replaceChildren();
      if (showPager) {
        for (let i = 1; i <= totalPages; i += 1) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = `board-page-num${i === page ? ' is-active' : ''}`;
          btn.textContent = String(i);
          btn.setAttribute('aria-label', `第 ${i} 页`);
          if (i === page) btn.setAttribute('aria-current', 'page');
          btn.addEventListener('click', () => {
            page = i;
            render();
          });
          nums.appendChild(btn);
        }
      }

      prevBtn.disabled = page <= 1 || matched.length === 0;
      nextBtn.disabled = page >= totalPages || matched.length === 0;
    };

    prevBtn.addEventListener('click', () => {
      page -= 1;
      render();
    });
    nextBtn.addEventListener('click', () => {
      page += 1;
      render();
    });
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        query = normalizeQuery(searchInput.value);
        page = 1;
        render();
      });
    }

    render();
  };

  initBoardPager();
})();
