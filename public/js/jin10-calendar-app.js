(() => {
  const API = '/api/public/v1/jin10-calendar';
  const MCP_API = '/api/public/v1/jin10-mcp-calendar';
  const dateInput = document.getElementById('calendar-date');
  const list = document.getElementById('calendar-list');
  const status = document.getElementById('calendar-status');
  const refresh = document.getElementById('calendar-refresh');
  const previous = document.getElementById('calendar-prev');
  const today = document.getElementById('calendar-today');
  const count = document.getElementById('calendar-count');
  const dataCount = document.getElementById('calendar-data-count');
  const eventCount = document.getElementById('calendar-event-count');
  const importantCount = document.getElementById('calendar-important-count');
  const filterButtons = [...document.querySelectorAll('[data-calendar-filter]')];
  let currentItems = [];
  let activeFilter = 'important';
  if (!dateInput || !list || !status) return;

  const beijingDate = (date = new Date()) => new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(date);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const displayValue = (value, unit) => value === null || value === undefined || value === '' ? '—' : `${escapeHtml(value)}${escapeHtml(unit || '')}`;
  const itemTime = (value) => value && value.length >= 16 ? value.slice(11, 16) : '待定';
  const itemDate = (value) => value && value.length >= 10 ? value.slice(0, 10) : '';
  const changeDate = (value, days) => {
    const date = new Date(`${value}T12:00:00+08:00`);
    date.setUTCDate(date.getUTCDate() + days);
    return beijingDate(date);
  };

  /** Merge MCP affect_txt into daily items by matching time + title. */
  const mergeAffectTxt = (items, mcpItems) => {
    const map = new Map();
    for (const item of mcpItems || []) {
      const time = String(item.time || '').slice(0, 16);
      if (!time) continue;
      const title = String(item.title || '');
      const key = `${time}|${title}`;
      if (!map.has(key)) map.set(key, item);
    }
    // Build a fuzzy map: time -> [MCP items] for subtitle matching
    const timeMap = new Map();
    for (const item of mcpItems || []) {
      const time = String(item.time || '').slice(0, 16);
      if (!time) continue;
      if (!timeMap.has(time)) timeMap.set(time, []);
      timeMap.get(time).push(item);
    }
    return (items || []).map((item) => {
      const key = `${String(item.time || '').slice(0, 16)}|${String(item.title || '')}`;
      let match = map.get(key);
      // Fuzzy fallback: same time, check if MCP title contains daily title or vice versa
      if (!match) {
        const time = String(item.time || '').slice(0, 16);
        const itemTitle = String(item.title || '');
        const candidates = timeMap.get(time) || [];
        match = candidates.find((mcp) => {
          const mcpTitle = String(mcp.title || '');
          return mcpTitle.includes(itemTitle) || itemTitle.includes(mcpTitle);
        });
      }
      if (!match) return item;
      return {
        ...item,
        affect_txt: item.affect_txt || match.affect_txt || null,
      };
    });
  };

  const renderItem = (item) => {
    const typeLabel = item.type === 'data' ? '数据' : item.type === 'event' ? '事件' : item.type === 'holiday' ? '假期' : '其他';
    const values = item.type === 'data' ? `<div class="calendar-values"><span>前值 <b>${displayValue(item.previous, item.unit)}</b></span><span>预期 <b>${displayValue(item.consensus, item.unit)}</b></span><span>公布 <b>${displayValue(item.actual, item.unit)}</b></span></div>` : '';
    const impactText = item.impact || item.affect_txt || null;
    // 金十 impact 方向按金银计算；美元方向为金银的镜像（美元强→金银弱）。
    // 双标签同时显示：利多金银 + 利空美元 / 利空金银 + 利多美元。
    const impact = impactText ? (() => {
      const cls = impactText === '利空' ? 'bearish' : impactText === '利多' ? 'bullish' : 'neutral';
      const reverse = impactText === '利多' ? '利空' : impactText === '利空' ? '利多' : impactText;
      const reverseCls = cls === 'bearish' ? 'bullish' : cls === 'bullish' ? 'bearish' : 'neutral';
      return `<div class="impact-row"><span class="impact-tag impact-${cls}">${escapeHtml(impactText)}金银</span><span class="impact-tag impact-${reverseCls}">${escapeHtml(reverse)}美元</span></div>`;
    })() : '';
    const safeStar = Math.max(0, Math.min(5, Math.trunc(Number(item.star) || 0)));
    const stars = safeStar ? '★'.repeat(safeStar) : '—';
    // Polymarket 概率匹配：美国 CPI 类事件
    const PM_KEYWORDS = [
      { re: /未季调核心CPI年率|核心CPI年率/i, q: 'core-cpi-yoy' },
      { re: /季调后核心CPI月率|核心CPI月率/i, q: 'core-cpi-mom' },
      { re: /未季调CPI年率|CPI年率/i, q: 'cpi-yoy' },
      { re: /季调后CPI月率|CPI月率/i, q: 'cpi-mom' },
    ];
    const pmMatch = item.country === '美国' ? PM_KEYWORDS.find((k) => k.re.test(item.title)) : null;
    const pmProb = pmMatch
      ? `<div class="pm-prob" data-slug="${pmMatch.q}"><span class="pm-label">Poly</span><span class="pm-value">…</span></div>`
      : '';
    return `<article class="calendar-item" data-type="${escapeHtml(item.type)}">
      <div class="calendar-time">${escapeHtml(itemTime(item.time))}<small>${escapeHtml(itemDate(item.time))}</small></div>
      <div><div class="calendar-title"><span class="type-tag">${typeLabel}</span>${escapeHtml(item.title)}</div><div class="calendar-meta">${escapeHtml(item.country || '全球')}${item.time_status ? ` · ${escapeHtml(item.time_status)}` : ''}</div>${pmProb}${values}${impact}</div>
      <div class="stars" aria-label="重要性${escapeHtml(item.star || 0)}星">${stars}</div>
    </article>`;
  };

  // 加载 Polymarket 概率并填入 .pm-prob（通过自家端点代理 gamma-api）
  const loadPolymarket = async () => {
    const el = list.querySelector('.pm-prob:not([data-loaded])');
    if (!el) return;
    el.setAttribute('data-loaded', '1');
    const slug = el.getAttribute('data-slug');
    try {
      const resp = await fetch(`/api/public/v1/polymarket-prob?q=${slug}`, { credentials: 'omit' });
      if (!resp.ok) { el.querySelector('.pm-value').textContent = 'N/A'; return; }
      const d = await resp.json();
      const v = el.querySelector('.pm-value');
      if (d.ok && d.top) {
        const q = d.top.question.replace(/^Will (?:annual |monthly |Core CPI )?inflation be /i, '').replace(/\?$/, '');
        v.textContent = `${q}: ${d.top.yes_prob}%`;
        v.title = `${d.event} · ${d.top.question}\nYes 概率 ${d.top.yes_prob}% · No ${d.top.no_prob}%`;
      } else {
        v.textContent = 'N/A';
      }
    } catch (e) {
      el.querySelector('.pm-value').textContent = 'N/A';
    }
    loadPolymarket(); // 继续下一项
  };

  // 只显示中国和美国的信息，其余屏蔽。
  const isCnUs = (item) => ['中国', '美国'].includes(String(item.country || ''));

  const filteredItems = () => currentItems.filter((item) => {
    if (!isCnUs(item)) return false;
    if (activeFilter === 'important') return ['data', 'event'].includes(item.type) && Number(item.star) >= 3;
    if (activeFilter === 'important-data') return item.type === 'data' && Number(item.star) >= 3;
    if (activeFilter === 'important-event') return item.type === 'event' && Number(item.star) >= 3;
    return true;
  });

  const renderList = () => {
    const items = filteredItems();
    const label = activeFilter === 'important' ? '重要总览' : activeFilter === 'important-data' ? '重要数据' : activeFilter === 'important-event' ? '重要事件' : '全部';
    list.innerHTML = items.length ? items.map(renderItem).join('') : `<div class="empty">当日暂无${label}</div>`;
    status.textContent = `${dateInput.value} · ${label} ${items.length} 项 · 北京时间`;
    loadPolymarket();
  };

  const load = async () => {
    const date = dateInput.value || beijingDate();
    dateInput.value = date;
    refresh?.setAttribute('disabled', '');
    status.textContent = `正在加载 ${date}…`;
    list.innerHTML = '';
    try {
      // MCP affect_txt is a nice-to-have enrichment; never block the main calendar.
      // Bound it with a 6s abort so a slow/hung MCP upstream degrades to direction-less rows.
      const mcpController = new AbortController();
      const mcpTimer = setTimeout(() => mcpController.abort(), 6000);
      const [mainRes, mcpRes] = await Promise.all([
        fetch(`${API}?date=${encodeURIComponent(date)}&t=${Date.now()}`, { cache: 'no-store' }),
        fetch(`${MCP_API}?t=${Date.now()}`, { cache: 'no-store', signal: mcpController.signal }).catch(() => null),
      ]);
      clearTimeout(mcpTimer);
      const payload = await mainRes.json();
      if (!mainRes.ok || payload.status !== 'ok') throw new Error(payload.error || '加载失败');
      let items = Array.isArray(payload.items) ? payload.items : [];
      if (mcpRes && mcpRes.ok) {
        const mcpPayload = await mcpRes.json();
        if (mcpPayload.status === 'ok' && Array.isArray(mcpPayload.items)) {
          items = mergeAffectTxt(items, mcpPayload.items);
        }
      }
      currentItems = items;
      // 统计只基于中国+美国条目（与列表过滤一致）。
      const cnUsItems = items.filter(isCnUs);
      count.textContent = String(cnUsItems.length);
      dataCount.textContent = String(cnUsItems.filter((item) => item.type === 'data').length);
      eventCount.textContent = String(cnUsItems.filter((item) => item.type === 'event').length);
      importantCount.textContent = String(cnUsItems.filter((item) => Number(item.star) >= 3).length);
      renderList();
      const nextUrl = new URL(location.href);
      nextUrl.searchParams.set('date', date);
      history.replaceState(null, '', nextUrl);
    } catch (error) {
      status.textContent = `加载失败：${error instanceof Error ? error.message : '网络异常'}`;
      list.innerHTML = '<div class="empty">财经日历暂时不可用，请稍后刷新。</div>';
      count.textContent = dataCount.textContent = eventCount.textContent = importantCount.textContent = '—';
    } finally {
      refresh?.removeAttribute('disabled');
    }
  };

  const initial = new URL(location.href).searchParams.get('date');
  dateInput.value = /^\d{4}-\d{2}-\d{2}$/.test(initial || '') ? initial : beijingDate();
  refresh?.addEventListener('click', load);
  dateInput.addEventListener('change', load);
  previous?.addEventListener('click', () => { dateInput.value = changeDate(dateInput.value, -1); load(); });
  today?.addEventListener('click', () => { dateInput.value = beijingDate(); load(); });
  filterButtons.forEach((button) => button.addEventListener('click', () => {
    activeFilter = button.dataset.calendarFilter || 'important';
    filterButtons.forEach((item) => item.classList.toggle('active', item === button));
    renderList();
  }));
  load();
})();
