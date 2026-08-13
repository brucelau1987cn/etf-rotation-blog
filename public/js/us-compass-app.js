/**
 * US compass live overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
(function () {
  const adapter = window.EtfQuote;
  if (!adapter?.normalizeQuotePayload) {
    console.error('EtfQuote adapter missing');
    return;
  }
  const { normalizeQuotePayload } = adapter;
  const US_LIVE_URL = '/api/public/v1/quote';
  const usLiveStatus = document.getElementById('us-live-status');
  const usLiveCards = [...document.querySelectorAll('[data-us-live-card]')];
  const usLiveSymbols = [...new Set(usLiveCards.map((card) => card.dataset.symbol).filter(Boolean))];
  const US_LIVE_BATCH_URL = `${US_LIVE_URL}?symbols=${encodeURIComponent(usLiveSymbols.join(','))}`;
  let usLiveLoading = false;
  const num = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const nyClock = () => {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
    return { weekday: parts.weekday, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
  };
  const isUsTradingWindow = () => {
    const { weekday, minutes } = nyClock();
    return !['Sat', 'Sun'].includes(weekday) && minutes >= 9 * 60 + 30 && minutes <= 16 * 60 + 5;
  };
  const nyTime = (value) => {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return '刚刚';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).format(date);
  };
  const setUsLiveStatus = (text, state = '') => {
    if (!usLiveStatus) return;
    usLiveStatus.textContent = text;
    usLiveStatus.classList.toggle('online', state === 'online');
    usLiveStatus.classList.toggle('error', state === 'error');
  };
  const sectionPriority = ['exit', 'harvest', 'ready_harvest', 'plant', 'ready_plant'];
  const sectionTone = { exit: 'exit', harvest: 'harvest', plant: 'plant', ready_harvest: 'ready', ready_plant: 'watch' };
  const realtimeActionCopy = {
    exit: '实时跌破防守线，优先撤退并复核',
    harvest: '实时达到兑现位，进入分批兑现',
    plant: '前一收盘已确认，按计划持有或回踩分批',
    ready_harvest: '接近兑现位，保持止盈观察',
    ready_plant: '等待价格进入伏击区',
  };
  const realtimeBasisCopy = {
    exit: '实时价≤防守线',
    harvest: '实时价≥兑现位',
    plant: '前一交易日触及伏击位且收盘重新站回',
    ready_harvest: '距兑现位3%以内或短期过热',
    ready_plant: '距伏击位3%以内，等待分批进入',
  };
  const sectionLabel = {
    exit: '防守线',
    harvest: '兑现位',
    plant: '伏击位',
    ready_harvest: '兑现位',
    ready_plant: '伏击位',
  };
  function moveUsCard(card, nextSection) {
    if (!card.dataset.originSection || card.dataset.currentSection === nextSection) return;
    const grid = document.getElementById(`us-grid-${nextSection}`);
    if (!grid) return;
    grid.appendChild(card);
    card.dataset.currentSection = nextSection;
    card.classList.remove('exit', 'harvest', 'plant', 'ready');
    card.classList.add(sectionTone[nextSection] || 'ready');
    const basisNode = card.querySelector('.basis');
    if (basisNode) {
      const label = sectionLabel[nextSection] || '关键位';
      basisNode.innerHTML = `<strong>${label}触发：</strong>${realtimeBasisCopy[nextSection] || '按实时价格重判'}`;
    }
  }
  function refreshUsSections() {
    for (const key of sectionPriority) {
      const grid = document.getElementById(`us-grid-${key}`);
      const section = document.getElementById(`us-section-${key}`);
      const count = grid ? grid.querySelectorAll(':scope > [data-us-live-card]').length : 0;
      if (section) section.hidden = count === 0;
      const sectionCount = document.getElementById(`us-section-count-${key}`);
      if (sectionCount) sectionCount.textContent = `${count}只`;
      const sectionHeading = document.getElementById(`signal-${key}`);
      if (sectionHeading) sectionHeading.textContent = `${section?.dataset.sectionTitle || ''} ${count}`;
      const topCount = document.getElementById(`us-count-${key}`);
      if (topCount) topCount.textContent = String(count);
    }
    const firstKey = sectionPriority.find((key) => document.querySelector(`#us-grid-${key} > [data-us-live-card]`));
    const firstCard = firstKey ? document.querySelector(`#us-grid-${firstKey} > [data-us-live-card]`) : null;
    const label = document.getElementById('us-top-action-label');
    const text = document.getElementById('us-top-action-text');
    if (label) label.textContent = firstKey ? `第一优先级｜${document.getElementById(`us-section-${firstKey}`)?.dataset.sectionTitle || ''}` : '今日动作';
    if (text) text.textContent = firstCard ? `${firstCard.dataset.symbol} · ${realtimeActionCopy[firstKey]}` : '没有触发类信号，保持观察。';
  }
  function applyUsLive(payload) {
    const liveMap = new Map((payload.items || []).map(item => [String(item.symbol), item]));
    const mode = isUsTradingWindow() ? '盘中实时' : '最新快照';
    const statusTime = nyTime(payload.generated_at);
    let updated = 0;
    for (const card of usLiveCards) {
      const symbol = card.dataset.symbol;
      const quote = liveMap.get(symbol);
      const price = num(quote?.price);
      if (price === null || !(price > 0)) continue;
      const priceNode = document.getElementById(`us-live-price-${symbol}`);
      const metaNode = document.getElementById(`us-live-meta-${symbol}`);
      const triggerNode = document.getElementById(`us-live-trigger-${symbol}`);
      if (priceNode) {
        priceNode.textContent = `$${price.toFixed(2)}`;
        priceNode.classList.add('updated');
      }
      const change = num(quote?.change_pct ?? quote?.change_percent);
      if (metaNode) {
        const changeText = change === null ? '' : ` · <span class="${change >= 0 ? 'up' : 'down'}">${change >= 0 ? '+' : ''}${change.toFixed(2)}%</span>`;
        const quoteClock = nyTime(quote?.quote_time);
        metaNode.innerHTML = card.classList.contains('secondary-card')
          ? `${quoteClock.slice(0, 5)} ET${changeText}`
          : `${mode} ${quoteClock} ET${changeText}`;
      }
      if (triggerNode) {
        const origin = card.dataset.originSection || card.dataset.side || '';
        const support = num(card.dataset.support);
        const target = num(card.dataset.target);
        const stop = num(card.dataset.stop);
        let nextSection = origin;
        let triggerText = triggerNode.textContent || '观察';
        if (stop !== null && price <= stop) {
          nextSection = 'exit';
          triggerText = '破位撤退';
        } else if (origin.includes('harvest') && target !== null) {
          const gap = (price / target - 1) * 100;
          if (price >= target) nextSection = 'harvest';
          triggerText = price >= target ? '达到兑现位' : (Math.abs(gap) <= 3 ? '距兑现位≤3%' : '等待兑现');
        } else if (origin === 'ready_plant' && support !== null) {
          const gap = (price / support - 1) * 100;
          const dayLow = num(quote?.low);
          const touched = dayLow !== null && dayLow <= support && (stop === null || price > stop);
          nextSection = 'ready_plant';
          triggerText = touched
            ? (price >= support ? '盘中已触价并站回·等待收盘确认' : '盘中触价·等待收盘确认')
            : (Math.abs(gap) <= 3 ? '距伏击位≤3%' : '等待伏击');
        } else if (origin === 'plant' && support !== null) {
          nextSection = 'plant';
          triggerText = price <= support ? '正式伏击回踩' : '正式伏击持有';
        }
        moveUsCard(card, nextSection);
        triggerNode.textContent = triggerText;
      }
      updated += 1;
    }
    refreshUsSections();
    setUsLiveStatus(`${mode} ${statusTime} ET · ${updated}/${usLiveCards.length}`, 'online');
  }
  async function loadUsLive() {
    if (usLiveLoading || document.hidden) return;
    usLiveLoading = true;
    try {
      if (!usLiveSymbols.length) throw new Error('无实时标的');
      const response = await fetch(US_LIVE_BATCH_URL);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = normalizeQuotePayload(await response.json());
      if (!payload.ok || !Array.isArray(payload.items) || !payload.items.length) throw new Error('行情格式异常');
      applyUsLive(payload);
    } catch (error) {
      setUsLiveStatus('实时行情暂不可用 · 保留收盘快照', 'error');
      console.warn('US ETF live quote overlay failed', error);
    } finally {
      usLiveLoading = false;
    }
  }
  async function refreshUsSymbol(symbol, button) {
    try {
      const calendar = await window.EtfLivePoll?.getCalendar?.('US');
      const phase = window.EtfLivePoll?.marketPhase?.('US', calendar);
      if (phase && !phase.active) {
        setUsLiveStatus(phase.label || '当前休市');
        return;
      }
    } catch (_) {
      setUsLiveStatus('交易日历暂不可用', 'error');
      return;
    }
    if (!symbol || button.classList.contains('loading')) return;
    button.classList.add('loading');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    setUsLiveStatus(`正在更新 ${symbol}…`);
    try {
      const response = await fetch(`${US_LIVE_URL}?symbol=${encodeURIComponent(symbol)}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = normalizeQuotePayload(await response.json());
      if (!payload.ok || !Array.isArray(payload.items) || !payload.items.length) throw new Error('单股行情格式异常');
      applyUsLive(payload);
      const quote = payload.items[0];
      setUsLiveStatus(`${symbol} 已更新 ${nyTime(quote.quote_time)} ET`, 'online');
    } catch (error) {
      setUsLiveStatus(`${symbol} 更新失败 · 保留现有价格`, 'error');
      console.warn('US ETF single quote refresh failed', error);
    } finally {
      button.classList.remove('loading');
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  }
  document.querySelectorAll('[data-live-refresh]').forEach((button) => {
    button.addEventListener('click', () => void refreshUsSymbol(button.dataset.liveRefresh, button));
  });
  if (window.EtfLivePoll?.startMarketPoll) {
    window.EtfLivePoll.startMarketPoll({
      market: 'US', intervalMs: 15_000, tick: loadUsLive,
      onStatus: (text, state) => setUsLiveStatus(text, state?.active ? 'online' : ''),
    });
  } else {
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void loadUsLive(); });
    void loadUsLive();
    window.setInterval(loadUsLive, 30_000);
  }
})();
    
