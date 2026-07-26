/**
 * A-share rolling energy terminal client app.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
(function() {
  const { normalizeQuotePayload, findQuoteItem } = window.EtfQuote || {};
  if (!normalizeQuotePayload || !findQuoteItem) {
    console.error('EtfQuote adapter missing');
    return;
  }

  const INSTRUMENTS = {
    '600021': { name: '上海电力', exchange: 'SSE', symbol: '600021' },
    '002173': { name: '创新医疗', exchange: 'SZSE', symbol: '002173' },
    '600703': { name: '三安光电', exchange: 'SSE', symbol: '600703' },
  };

  const params = new URLSearchParams(window.location.search);
  let currentSymbol = String(params.get('symbol') || params.get('code') || '600021').replace(/\.(SH|SZ|SS)$/i, '');
  if (!INSTRUMENTS[currentSymbol]) currentSymbol = '600021';

  const instrumentSelect = document.getElementById('instrument-select');
  if (instrumentSelect) instrumentSelect.value = currentSymbol;

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

  const setInstrumentHeader = (instrument) => {
    const nameEl = document.getElementById('inst-name');
    const symbolEl = document.getElementById('inst-symbol');
    if (nameEl) nameEl.textContent = instrument?.instrument_name || INSTRUMENTS[currentSymbol]?.name || '—';
    if (symbolEl) symbolEl.textContent = instrument?.symbol || currentSymbol;
    if (instrumentSelect && instrument?.symbol) instrumentSelect.value = instrument.symbol;
  };

  const updateUI = (data) => {
    if (!data) return;
    if (data.instrument) setInstrumentHeader(data.instrument);

    const pillAsOf = document.getElementById('pill-as-of');
    if (pillAsOf) pillAsOf.textContent = `数据时点：${formatTime(data.data_as_of)}`;

    const pillMode = document.getElementById('pill-mode');
    if (pillMode) pillMode.textContent = `模式：${data.mode === 'demo' ? '基准演示' : '实时数据'}`;

    const liveStatus = document.getElementById('live-status-pill');
    if (liveStatus) {
      liveStatus.textContent = '● 实时已同步';
      liveStatus.style.color = '#389e0d';
      liveStatus.style.borderColor = '#b7eb8f';
      liveStatus.style.background = '#f6ffed';
    }

    if (data.transmission) {
      const elCurrent = document.getElementById('stat-current-code');
      if (elCurrent) elCurrent.textContent = data.transmission.current_cycle_code || '—';

      const elLit = document.getElementById('stat-lit-count');
      if (elLit) {
        const litText = `连续点亮 ${data.transmission.lit_count} 格`;
        const latestTime = data.transmission.latest_triggered_at ? `(${formatTime(data.transmission.latest_triggered_at, true, true)})` : '';
        elLit.innerHTML = `${litText} <span id="stat-lit-time" style="font-style:normal; margin-left:0.25rem; color:#cf1322; font-weight:700;">${latestTime}</span>`;
      }

      const elStopped = document.getElementById('stat-stopped-code');
      if (elStopped) elStopped.textContent = data.transmission.state === 'transmitting' ? '多头推进中' : '观察中';
    }

    if (data.sell_chain) {
      const sellNodes = Array.isArray(data.sell_chain.nodes) ? data.sell_chain.nodes : [];
      const elStar = document.getElementById('stat-star-code');
      if (elStar) elStar.textContent = sellNodes.length > 0 ? `${sellNodes[0].code} 级` : '未触发';

      const elStarTime = document.getElementById('stat-star-time');
      if (elStarTime) {
        elStarTime.textContent = sellNodes.length > 0 ? `${formatTime(sellNodes[0].triggered_at, true, true)}` : '等待买方信号后再出';
      }

      const elSummary = document.getElementById('stat-sell-summary');
      if (elSummary) elSummary.textContent = `${sellNodes.length} 个`;
    }

    const buyContainer = document.getElementById('buy-cells-container');
    const sellContainer = document.getElementById('sell-cells-container');
    const rawTimeline = Array.isArray(data.timeline) ? data.timeline : [];
    let timeline = rawTimeline;
    if (timeline.length === 0) {
      const buyItems = (data.cycles || data.buy_cycles || []).map(c => ({ type: 'BUY', code: c.cycle_code, triggered_at: c.buy_triggered_at }));
      const sellItems = (data.sell_chain?.nodes || []).map(n => ({ type: 'SELL', code: n.code, triggered_at: n.triggered_at }));
      timeline = [...buyItems, ...sellItems].sort((a, b) => new Date(a.triggered_at).getTime() - new Date(b.triggered_at).getTime());
    }

    if (buyContainer && sellContainer) {
      buyContainer.replaceChildren();
      sellContainer.replaceChildren();

      if (timeline.length === 0) {
        const emptyBuy = document.createElement('div');
        emptyBuy.style.cssText = 'font-size: 0.8rem; color: #94a3b8; padding: 0.6rem 0.5rem; display: flex; align-items: center;';
        emptyBuy.textContent = '观察中，暂无买入点亮节点';
        buyContainer.appendChild(emptyBuy);

        const emptySell = document.createElement('div');
        emptySell.style.cssText = 'font-size: 0.8rem; color: #94a3b8; padding: 0.6rem 0.5rem; display: flex; align-items: center;';
        emptySell.textContent = '等待多方信号触发中...';
        sellContainer.appendChild(emptySell);
      } else {
        timeline.forEach(item => {
          if (item.type === 'BUY') {
            const buyBadge = document.createElement('div');
            buyBadge.className = 'cell-badge buy-buy';
            buyBadge.dataset.buyCode = item.code;
            const codeEl = document.createElement('div');
            codeEl.className = 'badge-code';
            codeEl.textContent = item.code;
            const timeEl = document.createElement('div');
            timeEl.className = 'badge-time';
            timeEl.textContent = formatTime(item.triggered_at, true, true);
            buyBadge.append(codeEl, timeEl);
            buyContainer.appendChild(buyBadge);
            const sellSpacer = document.createElement('div');
            sellSpacer.style.cssText = 'min-width: 105px; visibility: hidden; pointer-events: none;';
            sellContainer.appendChild(sellSpacer);
          } else if (item.type === 'SELL') {
            const sellBadge = document.createElement('div');
            sellBadge.className = `cell-badge sell-${(item.sell_state || 'sell').toLowerCase()}`;
            sellBadge.dataset.sellCode = item.code;
            const codeEl = document.createElement('div');
            codeEl.className = 'badge-code';
            codeEl.textContent = item.code;
            const timeEl = document.createElement('div');
            timeEl.className = 'badge-time';
            timeEl.textContent = formatTime(item.triggered_at, true, true);
            sellBadge.append(codeEl, timeEl);
            sellContainer.appendChild(sellBadge);
            const buySpacer = document.createElement('div');
            buySpacer.style.cssText = 'min-width: 105px; visibility: hidden; pointer-events: none;';
            buyContainer.appendChild(buySpacer);
          }
        });
      }
    }

    if (Array.isArray(data.sell_alerts)) {
      const container = document.getElementById('ai-alerts-container');
      if (container) {
        container.replaceChildren();
        if (data.sell_alerts.length === 0) {
          const empty = document.createElement('div');
          empty.style.cssText = 'color: #64748b; font-size: 0.88rem; text-align: center; padding: 1.5rem;';
          empty.textContent = '当前多头传导有序，暂无空方卖出预警。';
          container.appendChild(empty);
        } else {
          data.sell_alerts.forEach(alert => {
            const box = document.createElement('div');
            box.className = 'ai-alert-box';
            const meta = document.createElement('div');
            meta.className = 'ai-alert-meta';
            const span1 = document.createElement('span');
            span1.textContent = '触发节点：';
            const s1 = document.createElement('strong');
            s1.textContent = `${alert.cycle_code} (${alert.timeframe_minutes}分钟)`;
            span1.appendChild(s1);
            const span2 = document.createElement('span');
            span2.textContent = '多头极限位置：';
            const s2 = document.createElement('strong');
            s2.textContent = alert.transmission_position || '—';
            span2.appendChild(s2);
            const span3 = document.createElement('span');
            span3.textContent = '今日剩余交易时间：';
            const s3 = document.createElement('strong');
            s3.textContent = `${alert.remaining_session_minutes}分钟`;
            span3.appendChild(s3);
            meta.append(span1, span2, span3);
            const p = document.createElement('p');
            p.className = 'ai-alert-text';
            p.textContent = alert.analysis;
            box.append(meta, p);
            container.appendChild(box);
          });
        }
      }
    }
  };

  const updateQuoteUI = (payload) => {
    const item = findQuoteItem(payload, currentSymbol) || payload?.items?.[0] || null;
    if (!item || typeof item.price !== 'number') {
      const badge = document.getElementById('stock-quote-badge');
      const pill = document.getElementById('pill-stock-quote');
      if (badge) badge.textContent = '行情暂不可用';
      if (pill) {
        pill.textContent = '行情暂不可用';
        pill.style.color = '#64748b';
        pill.style.background = '#f1f5f9';
        pill.style.borderColor = '#cbd5e1';
      }
      return;
    }
    const changePct = typeof item.change_percent === 'number' ? item.change_percent : item.change_pct;
    const badge = document.getElementById('stock-quote-badge');
    const pill = document.getElementById('pill-stock-quote');
    const isUp = changePct > 0;
    const isDown = changePct < 0;
    const color = isUp ? '#cf1322' : isDown ? '#389e0d' : '#475569';
    const bg = isUp ? '#fff1f0' : isDown ? '#f6ffed' : '#f1f5f9';
    const border = isUp ? '#ffa39e' : isDown ? '#b7eb8f' : '#cbd5e1';
    const sign = isUp ? '+' : '';
    const text = `¥${item.price.toFixed(2)} ${sign}${typeof changePct === 'number' ? changePct.toFixed(2) : '0.00'}%`;
    if (badge) {
      badge.textContent = text;
      badge.style.color = color;
    }
    if (pill) {
      pill.textContent = `实时行情：${text}`;
      pill.style.color = color;
      pill.style.background = bg;
      pill.style.borderColor = border;
    }
  };

  const fetchStockQuote = async () => {
    if (document.hidden) return;
    const meta = INSTRUMENTS[currentSymbol] || INSTRUMENTS['600021'];
    try {
      const res = await fetch(`/api/public/v1/quote?symbol=${encodeURIComponent(meta.symbol)}&exchange=${encodeURIComponent(meta.exchange)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      updateQuoteUI(normalizeQuotePayload(await res.json()));
    } catch (e) {
      updateQuoteUI(null);
    }
  };

  let inFlight = false;
  const fetchLatestSignals = async () => {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const res = await fetch(`/api/public/v1/rolling-signals?symbol=${encodeURIComponent(currentSymbol)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) return;
      updateUI(await res.json());
    } catch (e) {
      // keep DOM
    } finally {
      inFlight = false;
    }
  };

  const switchInstrument = async (symbol) => {
    currentSymbol = INSTRUMENTS[symbol] ? symbol : '600021';
    const url = new URL(window.location.href);
    url.searchParams.set('symbol', currentSymbol);
    window.history.replaceState({}, '', url);
    setInstrumentHeader(INSTRUMENTS[currentSymbol]);
    await Promise.all([fetchLatestSignals(), fetchStockQuote()]);
  };

  if (instrumentSelect) {
    instrumentSelect.addEventListener('change', () => {
      void switchInstrument(instrumentSelect.value);
    });
  }

  const tableWrapper = document.getElementById('table-outer-wrapper');
  const toggleBtn = document.getElementById('toggle-view-btn');
  let isCompact = localStorage.getItem('rolling_view_mode') !== 'full';
  const applyViewMode = () => {
    if (!tableWrapper || !toggleBtn) return;
    if (isCompact) {
      tableWrapper.classList.add('compact-mode');
      toggleBtn.textContent = '⚡ 模式：精简按需点亮';
    } else {
      tableWrapper.classList.remove('compact-mode');
      toggleBtn.textContent = '📊 模式：显示完整 34 轨道';
    }
  };
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      isCompact = !isCompact;
      localStorage.setItem('rolling_view_mode', isCompact ? 'compact' : 'full');
      applyViewMode();
    });
  }
  applyViewMode();
  setInstrumentHeader(INSTRUMENTS[currentSymbol]);

  setTimeout(function() {
    fetchLatestSignals();
    fetchStockQuote();
  }, 500);

  if (window.EtfLivePoll?.startLivePoll) {
    window.EtfLivePoll.startLivePoll({ intervalMs: 15000, immediate: false, tick: async () => { await fetchStockQuote(); }});
    window.EtfLivePoll.startLivePoll({ intervalMs: 30000, immediate: false, tick: async () => { await fetchLatestSignals(); }});
  } else {
    setInterval(fetchLatestSignals, 30000);
    setInterval(fetchStockQuote, 15000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        void fetchLatestSignals();
        void fetchStockQuote();
      }
    });
  }
})();
