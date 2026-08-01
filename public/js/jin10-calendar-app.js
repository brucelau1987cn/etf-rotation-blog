(() => {
  const API = '/api/public/v1/jin10-calendar';
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

  const renderItem = (item) => {
    const typeLabel = item.type === 'data' ? '数据' : item.type === 'event' ? '事件' : item.type === 'holiday' ? '假期' : '其他';
    const values = item.type === 'data' ? `<div class="calendar-values"><span>前值 <b>${displayValue(item.previous, item.unit)}</b></span><span>预期 <b>${displayValue(item.consensus, item.unit)}</b></span><span>公布 <b>${displayValue(item.actual, item.unit)}</b></span></div>` : '';
    const safeStar = Math.max(0, Math.min(5, Math.trunc(Number(item.star) || 0)));
    const stars = safeStar ? '★'.repeat(safeStar) : '—';
    return `<article class="calendar-item" data-type="${escapeHtml(item.type)}">
      <div class="calendar-time">${escapeHtml(itemTime(item.time))}<small>${escapeHtml(itemDate(item.time))}</small></div>
      <div><div class="calendar-title"><span class="type-tag">${typeLabel}</span>${escapeHtml(item.title)}</div><div class="calendar-meta">${escapeHtml(item.country || '全球')}${item.time_status ? ` · ${escapeHtml(item.time_status)}` : ''}</div>${values}</div>
      <div class="stars" aria-label="重要性${escapeHtml(item.star || 0)}星">${stars}</div>
    </article>`;
  };

  const filteredItems = () => currentItems.filter((item) => {
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
  };

  const load = async () => {
    const date = dateInput.value || beijingDate();
    dateInput.value = date;
    refresh?.setAttribute('disabled', '');
    status.textContent = `正在加载 ${date}…`;
    list.innerHTML = '';
    try {
      const response = await fetch(`${API}?date=${encodeURIComponent(date)}&t=${Date.now()}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || payload.status !== 'ok') throw new Error(payload.error || '加载失败');
      const items = Array.isArray(payload.items) ? payload.items : [];
      currentItems = items;
      count.textContent = String(items.length);
      dataCount.textContent = String(payload.counts?.data || 0);
      eventCount.textContent = String(payload.counts?.event || 0);
      importantCount.textContent = String(items.filter((item) => Number(item.star) >= 3).length);
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
