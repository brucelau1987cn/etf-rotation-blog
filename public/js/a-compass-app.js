/**
 * A-share compass live overlay client.
 * Refreshes every 15 seconds only during A-share auction/trading sessions.
 */
(function () {
  const EDGE_QUOTE_URL = '/api/public/v1/quote';
  const SESSION_URL = '/api/public/v1/market-session?market=CN_A';
  const REFRESH_MS = 15_000;
  const liveStatus = document.getElementById('live-quote-status');
  const liveCards = [...document.querySelectorAll('[data-live-card]')];
  const { normalizeQuotePayload, aShareSymbolsParam } = window.EtfQuote || {};
  let liveLoading = false;
  let calendarSession = null;
  let nextFetchAt = 0;
  let lastCalendarFetchAt = 0;
  let calendarLoading = false;
  let timer = null;

  const numberOf = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const localParts = (tz = 'Asia/Shanghai') => Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  const displaySource = (source) => String(source || 'stock-api').replace('@', ' v');
  const setStatus = (text, state = '') => {
    if (!liveStatus) return;
    liveStatus.textContent = text;
    liveStatus.classList.toggle('online', state === 'online');
    liveStatus.classList.toggle('error', state === 'error');
  };
  const clock = () => {
    const p = localParts();
    return {
      date: `${p.year}-${p.month}-${p.day}`,
      weekday: p.weekday,
      seconds: Number(p.hour) * 3600 + Number(p.minute) * 60 + Number(p.second),
    };
  };
  const toSeconds = (h, m, s = 0) => h * 3600 + m * 60 + s;
  const marketPhase = () => {
    const now = clock();
    const row = calendarSession?.session;
    if (!row || row.trade_date !== now.date || Number(row.is_open) !== 1) {
      return { active: false, label: '今日休市', next: calendarSession?.next_open_session?.open_at || null };
    }
    const t = now.seconds;
    if (t >= toSeconds(9, 15) && t < toSeconds(9, 25)) return { active: true, label: '开盘竞价' };
    if (t >= toSeconds(9, 25) && t < toSeconds(9, 30)) return { active: false, label: '等待开盘', resume: '09:30' };
    if (t >= toSeconds(9, 30) && t < toSeconds(11, 30)) return { active: true, label: '盘中实时' };
    if (t >= toSeconds(11, 30) && t < toSeconds(13, 0)) return { active: false, label: '午间休市', resume: '13:00' };
    if (t >= toSeconds(13, 0) && t < toSeconds(14, 57)) return { active: true, label: '盘中实时' };
    if (t >= toSeconds(14, 57) && t <= toSeconds(15, 0)) return { active: true, label: '收盘竞价' };
    if (t < toSeconds(9, 15)) return { active: false, label: '等待竞价', resume: '09:15' };
    return { active: false, label: '已收盘', next: calendarSession?.next_open_session?.open_at || null };
  };
  const formatNext = (value) => value ? `${String(value).slice(5, 10)} 09:15` : '';

  async function loadCalendar(force = false) {
    if (calendarLoading || (!force && Date.now() - lastCalendarFetchAt < 5 * 60_000)) return;
    calendarLoading = true;
    try {
      const res = await fetch(`${SESSION_URL}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`calendar HTTP ${res.status}`);
      calendarSession = await res.json();
      lastCalendarFetchAt = Date.now();
    } catch (error) {
      console.warn('market calendar unavailable', error);
      calendarSession = null;
    } finally {
      calendarLoading = false;
    }
  }

  function applyLive(payload, phaseLabel) {
    const liveMap = new Map((payload.items || []).map(item => [String(item.code), item]));
    const time = String(payload.generated_at || '').slice(11, 19) || '刚刚';
    let updated = 0;
    for (const card of liveCards) {
      const code = card.dataset.code;
      const quote = liveMap.get(code);
      const price = numberOf(quote?.price);
      if (price === null) continue;
      const side = card.dataset.side;
      const actionLevel = numberOf(card.dataset.actionLevel);
      const stop = numberOf(card.dataset.stop);
      const priceNode = document.getElementById(`live-price-${code}`);
      const distanceNode = document.getElementById(`live-distance-${code}`);
      const metaNode = document.getElementById(`live-meta-${code}`);
      const triggerNode = document.getElementById(`live-trigger-${code}`);
      if (priceNode) { priceNode.textContent = price.toFixed(3); priceNode.classList.add('updated'); }
      let triggered = false;
      let triggerText = '等待动作位';
      if (stop !== null && price <= stop) { triggered = true; triggerText = '破位撤退'; }
      else if (actionLevel !== null && actionLevel > 0) {
        const gap = (price / actionLevel - 1) * 100;
        if (distanceNode) distanceNode.textContent = `${gap >= 0 ? '高于' : '低于'}动作位 ${Math.abs(gap).toFixed(1)}%`;
        if (side === 'harvest') { triggered = price >= actionLevel; triggerText = triggered ? '达到兑现位' : (Math.abs(gap) <= 3 ? '距兑现位≤3%' : '等待兑现触发'); }
        else { triggered = price <= actionLevel; triggerText = triggered ? '进入伏击区' : (Math.abs(gap) <= 3 ? '距伏击位≤3%' : '等待伏击触发'); }
      }
      if (triggerNode) { triggerNode.textContent = triggerText; triggerNode.classList.toggle('triggered', triggered); }
      if (metaNode) {
        const change = numberOf(quote?.change_pct);
        const changeText = change === null ? '' : ` · <span class="${change > 0 ? 'market-up' : change < 0 ? 'market-down' : ''}">${change > 0 ? '+' : ''}${change.toFixed(2)}%</span>`;
        metaNode.innerHTML = `${phaseLabel} ${time} · ${displaySource(quote?.source || payload.source)}${changeText}`;
      }
      updated += 1;
    }
    nextFetchAt = Date.now() + REFRESH_MS;
    setStatus(`${phaseLabel} · 15s 后刷新 · ${updated}/${liveCards.length}`, 'online');
  }

  async function loadLive() {
    if (liveLoading || document.hidden) return;
    const phase = marketPhase();
    if (!phase.active) return;
    liveLoading = true;
    try {
      if (!normalizeQuotePayload || !aShareSymbolsParam) throw new Error('EtfQuote adapter missing');
      const codes = liveCards.map(c => c.dataset.code).filter(Boolean);
      if (!codes.length) return setStatus('无实时标的', 'error');
      const edgeRes = await fetch(`${EDGE_QUOTE_URL}?symbols=${encodeURIComponent(aShareSymbolsParam(codes))}&t=${Date.now()}`, { cache: 'no-store' });
      if (!edgeRes.ok) throw new Error(`Edge HTTP ${edgeRes.status}`);
      const normalized = normalizeQuotePayload(await edgeRes.json());
      const items = (normalized.items || []).filter(q => Number(q.price) > 0).map(q => ({
        code: q.code || q.symbol, price: q.price, change_pct: q.change_pct ?? q.change_percent,
        source: q.source || 'Cloudflare-Edge-Quote',
      }));
      if (!items.length) throw new Error('Edge 价格无效');
      applyLive({ generated_at: normalized.generated_at || new Date().toISOString(), source: normalized.source, items }, phase.label);
    } catch (error) {
      setStatus('实时行情暂不可用 · 保留静态快照', 'error');
      console.warn('ETF live quote overlay failed', error);
    } finally { liveLoading = false; }
  }

  function paintStatus() {
    if (document.hidden) return setStatus('页面后台暂停');
    if (!calendarSession) return setStatus('交易日历连接中');
    const phase = marketPhase();
    if (phase.active) {
      const remain = Math.max(0, Math.ceil((nextFetchAt - Date.now()) / 1000));
      setStatus(`${phase.label} · ${remain || 1}s 后刷新`, 'online');
    } else if (phase.resume) setStatus(`${phase.label} · ${phase.resume}恢复`);
    else if (phase.next) setStatus(`${phase.label} · ${formatNext(phase.next)}恢复`);
    else setStatus(phase.label);
  }

  async function tick() {
    await loadCalendar();
    const phase = marketPhase();
    if (phase.active && Date.now() >= nextFetchAt) await loadLive();
    paintStatus();
  }

  void (async () => { await loadCalendar(true); nextFetchAt = 0; await tick(); })();
  timer = window.setInterval(tick, 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { nextFetchAt = 0; void loadCalendar(true).then(tick); }
    else paintStatus();
  });
  window.addEventListener('pagehide', () => { if (timer) clearInterval(timer); }, { once: true });
})();
