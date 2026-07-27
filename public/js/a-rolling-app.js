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
      { name: '上证指数', symbol: '000001', querySymbol: '000001.SH' },
      { name: '深证成指', symbol: '399001', querySymbol: '399001.SZ' },
      { name: '创业板指', symbol: '399006', querySymbol: '399006.SZ' },
    ],
    futures: [
      { name: '黄金连续', symbol: 'AU0', querySymbol: 'nf_AU0' },
      { name: '原油连续', symbol: 'SC0', querySymbol: 'nf_SC0' },
      { name: '豆粕连续', symbol: 'M0', querySymbol: 'nf_M0' },
    ],
    hk: [
      { name: '恒生指数', symbol: 'HSI', querySymbol: 'HSI.HK' },
      { name: '恒生综合指数', symbol: 'HSCI', querySymbol: 'HSCI.HK' },
      { name: '恒生科技指数', symbol: 'HSTECH', querySymbol: 'HSTECH.HK' },
    ],
    us: [
      { name: '标普500指数', symbol: 'INX', querySymbol: 'INX.US' },
      { name: '纳斯达克综合指数', symbol: 'IXIC', querySymbol: 'IXIC.US' },
      { name: '道琼斯工业平均指数', symbol: 'DJI', querySymbol: 'DJI.US' },
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
      allBuys,
      allSells,
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

  const startSummaryTicker = (track) => {
    const oldTimer = summaryTickerTimers.get(track.id);
    if (oldTimer) clearInterval(oldTimer);
    track.style.transform = 'translateY(0)';
    const items = Array.from(track.children);
    if (items.length <= 3 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let offset = 0;
    const timer = setInterval(() => {
      if (document.hidden || track.closest('.signal-card')?.matches(':hover')) return;
      offset = (offset + 1) % items.length;
      track.style.transform = `translateY(${-offset * 30}px)`;
    }, 3000);
    summaryTickerTimers.set(track.id, timer);
  };

  const renderSummarySignals = (type, signals) => {
    const track = document.getElementById(type === 'BUY' ? 'buy-signal-track' : 'sell-signal-track');
    if (!track) return;
    track.replaceChildren();
    if (!signals.length) {
      const empty = document.createElement('div');
      empty.className = 'summary-signal-empty';
      empty.textContent = type === 'BUY' ? '等待多方信号' : '等待空方信号';
      track.appendChild(empty);
      startSummaryTicker(track);
      return;
    }
    signals.forEach((signal) => {
      const row = document.createElement('div');
      row.className = 'summary-signal-item';
      const name = document.createElement('span');
      name.className = 'summary-signal-name';
      name.textContent = signal.name;
      const point = document.createElement('strong');
      point.className = 'summary-signal-point';
      point.textContent = signal.code;
      const time = document.createElement('time');
      time.className = 'summary-signal-time';
      time.textContent = formatTime(signal.at, true, false);
      row.append(name, point, time);
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

  const renderTodaySignals = (type, signals) => {
    const track = document.getElementById(type === 'BUY' ? 'buy-today-track' : 'sell-today-track');
    if (!track) return;
    track.replaceChildren();
    if (!signals.length) {
      const empty = document.createElement('span');
      empty.className = 'today-signal-empty';
      empty.textContent = '暂无';
      track.appendChild(empty);
      return;
    }
    signals.forEach((signal) => {
      const row = document.createElement('div');
      row.className = 'today-signal-row';
      const name = document.createElement('span');
      name.className = 'today-signal-name';
      name.textContent = signal.name;
      const point = document.createElement('strong');
      point.className = 'today-signal-point';
      point.textContent = signal.code;
      const time = document.createElement('time');
      time.className = 'today-signal-time';
      time.textContent = formatTime(signal.at, false, false);
      row.append(name, point, time);
      track.appendChild(row);
    });
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
        liveStatus.textContent = `● ${INSTRUMENTS.length} 标的已同步`;
        liveStatus.style.color = '#389e0d';
        liveStatus.style.borderColor = '#b7eb8f';
        liveStatus.style.background = '#f6ffed';
      }
    }
    if (!payloads.length) {
      renderSummarySignals('BUY', []);
      renderSummarySignals('SELL', []);
      renderTodaySignals('BUY', []);
      renderTodaySignals('SELL', []);
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
        latest: [...split.buys, ...split.sells]
          .filter(item => item?.triggered_at)
          .sort((a, b) => new Date(b.triggered_at || 0).getTime() - new Date(a.triggered_at || 0).getTime())[0] || null,
      };
    });

    const signalsFor = (type) => rows
      .map((row) => {
        const payload = payloads.find(p => p?.instrument?.symbol === row.symbol) || null;
        const split = normalizeTimeline(payload || {});
        const latest = (type === 'BUY' ? split.buys : split.sells)
          .filter(item => item?.triggered_at)
          .sort((a, b) => new Date(b.triggered_at || 0).getTime() - new Date(a.triggered_at || 0).getTime())[0] || null;
        return latest ? { name: row.name, code: latest.code, at: latest.triggered_at } : null;
      })
      .filter(Boolean)
      .sort((a, b) => new Date(b.at || 0).getTime() - new Date(a.at || 0).getTime());

    renderSummarySignals('BUY', signalsFor('BUY'));
    renderSummarySignals('SELL', signalsFor('SELL'));

    const todaySignalsFor = (type) => rows
      .flatMap((row) => {
        const payload = payloads.find(p => p?.instrument?.symbol === row.symbol) || null;
        const split = normalizeTimeline(payload || {});
        return (type === 'BUY' ? split.allBuys : split.allSells)
          .filter(item => isTodayShanghai(item?.triggered_at))
          .map(item => ({ name: row.name, code: item.code, at: item.triggered_at }));
      })
      .sort((a, b) => new Date(b.at || 0).getTime() - new Date(a.at || 0).getTime());

    renderTodaySignals('BUY', todaySignalsFor('BUY'));
    renderTodaySignals('SELL', todaySignalsFor('SELL'));
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
    if (priceEl) priceEl.textContent = price.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (changeEl) {
      const amountText = Number.isFinite(changeAmount) ? `${changeAmount > 0 ? '+' : ''}${changeAmount.toFixed(2)}` : '—';
      const pctText = Number.isFinite(changePct) ? `${sign}${changePct.toFixed(2)}%` : '—';
      changeEl.textContent = `${amountText} · ${pctText}`;
      changeEl.className = `market-index-change ${direction}`;
    }
  };

  const fetchMarketIndices = async () => {
    const indexSymbols = INDEX_SETS[market] || INDEX_SETS.a;
    try {
      const symbols = indexSymbols.map(item => item.querySymbol).join(',');
      const res = await fetch(`/api/public/v1/quote?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const payload = normalizeQuotePayload(await res.json());
      indexSymbols.forEach(meta => updateMarketIndexUI(meta, findQuoteItem(payload, meta.symbol)));
    } catch {
      indexSymbols.forEach(meta => updateMarketIndexUI(meta, null));
    }
  };

  const QUOTE_INTERVAL_MS = 15000;
  const SIGNAL_INTERVAL_MS = 30000;
  let nextQuoteAt = Date.now() + QUOTE_INTERVAL_MS;
  let countdownTimer = null;

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
      if (indexPill) indexPill.textContent = '后台暂停';
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
      if (indexPill) indexPill.textContent = '刷新中…';
      return;
    }
    if (pill) {
      pill.textContent = `实时：${remainSec}s 后刷新`;
      pill.style.color = '#334155';
      pill.style.background = '#f1f5f9';
      pill.style.borderColor = '#cbd5e1';
    }
    if (indexPill) indexPill.textContent = `${remainSec}s 后刷新`;
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
      await Promise.all([
        ...INSTRUMENTS.map(async (meta) => {
          try {
            updateQuoteUI(meta.symbol, await fetchOneQuote(meta));
          } catch {
            updateQuoteUI(meta.symbol, null);
          }
        }),
        fetchMarketIndices(),
      ]);
    } finally {
      quoteInFlight = false;
      armQuoteCountdown();
    }
  };

  setTimeout(() => { fetchAllSignals(); }, 400);
  const initialQuoteLoad = setTimeout(() => { fetchAllQuotes(); }, 120);

  if (calendarMarket && window.EtfLivePoll?.startMarketPoll) {
    window.EtfLivePoll.startMarketPoll({
      market: calendarMarket,
      intervalMs: QUOTE_INTERVAL_MS,
      immediate: true,
      tick: async () => { await fetchAllQuotes(); },
      onStatus: (text) => {
        const pill = document.getElementById('pill-refresh-countdown');
        if (pill) pill.textContent = text;
      },
    });
    window.EtfLivePoll.startLivePoll({ intervalMs: SIGNAL_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllSignals(); }});
  } else if (window.EtfLivePoll?.startLivePoll) {
    // Futures (and any ungated market): free-running poll without stock session calendar.
    window.EtfLivePoll.startLivePoll({ intervalMs: QUOTE_INTERVAL_MS, immediate: true, tick: async () => { await fetchAllQuotes(); }});
    window.EtfLivePoll.startLivePoll({ intervalMs: SIGNAL_INTERVAL_MS, immediate: false, tick: async () => { await fetchAllSignals(); }});
  } else {
    setInterval(fetchAllSignals, SIGNAL_INTERVAL_MS);
    setInterval(fetchAllQuotes, QUOTE_INTERVAL_MS);
  }
})();
