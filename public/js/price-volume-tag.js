/**
 * 量价 12 态标签：遍历 .instrument-board 卡片，请求 price-volume-tag 端点，
 * 在每张卡片标题旁插入量价关系标签 pill。
 * 排除低筹码页（不使用 ARollingEnergyMatrix 组件，天然不受影响）。
 */
(function () {
  const EXCH_MAP = { SSE: '17', SZSE: '33' };
  let loaded = false;

  function normalizeAsOfDate(value) {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length < 8) return null;
    return {
      key: digits.slice(0, 8),
      label: `${digits.slice(4, 6)}/${digits.slice(6, 8)}`,
    };
  }

  function updateAsOfBadge(tags) {
    const badge = document.getElementById('price-volume-asof');
    if (!badge) return;
    const latest = Object.values(tags || {})
      .map((tag) => normalizeAsOfDate(tag?.date))
      .filter(Boolean)
      .sort((a, b) => b.key.localeCompare(a.key))[0];
    if (!latest) return;
    const detail = badge.querySelector('span');
    if (detail) detail.textContent = `· 截至 ${latest.label} 收盘`;
  }

  async function loadTags() {
    if (loaded) return;
    loaded = true;
    const boards = document.querySelectorAll('.instrument-board');
    const trackingSlots = document.querySelectorAll('[data-price-volume-symbol]');
    if (!boards.length && !trackingSlots.length) return;

    // 收集 symbols（A股：exchange→market code；期货：原样）
    const symbols = [];
    const boardMap = new Map(); // rawSymbol -> DOM element
    const trackingMap = new Map(); // rawSymbol -> low-chip tracking slots
    boards.forEach((board) => {
      const sym = board.getAttribute('data-symbol');
      const exchange = board.getAttribute('data-exchange');
      let raw;
      if (exchange === 'FUTURES') {
        raw = sym; // SI=F, GC=F, CL=F
      } else {
        const mkt = EXCH_MAP[exchange];
        if (mkt && sym) raw = `${mkt}_${sym}`;
      }
      if (raw) {
        symbols.push(raw);
        boardMap.set(raw, board);
      }
    });
    trackingSlots.forEach((slot) => {
      const raw = slot.getAttribute('data-price-volume-symbol');
      if (!raw) return;
      symbols.push(raw);
      const slots = trackingMap.get(raw) || [];
      slots.push(slot);
      trackingMap.set(raw, slots);
    });
    const uniqueSymbols = [...new Set(symbols)];
    if (!uniqueSymbols.length) return;

    try {
      const resp = await fetch(`/api/public/v1/price-volume-tag?s=${uniqueSymbols.join(',')}`, { credentials: 'omit' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.ok || !data.tags) return;
      updateAsOfBadge(data.tags);

      for (const [raw, tag] of Object.entries(data.tags)) {
        if (!tag.ok) continue;
        const tracking = trackingMap.get(raw) || [];
        tracking.forEach((slot) => {
          slot.className = `tc-vol-tag-slot vol-tag vol-tag-${tag.cls || 'amber'}`;
          slot.textContent = tag.name;
          slot.title = `量价12态 · ${tag.date || '最近收盘'} · 价 ${tag.pct_chg > 0 ? '+' : ''}${tag.pct_chg?.toFixed(2) || ''}% · 量比 ${tag.vol_ratio ?? '—'}`;
          slot.hidden = false;
        });

        const board = boardMap.get(raw);
        if (!board) continue;
        // 插入到「筹码指标」标题行的右侧（chip-panel-title 内）
        const chipPanel = board.querySelector('[data-role="chip"]');
        const titleEl = chipPanel ? chipPanel.querySelector('.chip-panel-title') : null;
        const nameEl = board.querySelector('[data-role="inst-name"]');
        const anchor = titleEl || nameEl;
        if (!anchor) continue;
        const parent = anchor.tagName === 'SPAN' ? anchor.parentElement : anchor;
        // 避免重复插入
        if (parent.querySelector('.vol-tag')) continue;
        const pill = document.createElement('span');
        pill.className = `vol-tag vol-tag-${tag.cls || 'amber'}`;
        pill.textContent = tag.name;
        pill.title = `价 ${tag.pct_chg > 0 ? '+' : ''}${tag.pct_chg?.toFixed(2) || ''}% · 量比 ${tag.vol_ratio ?? '—'}`;
        if (titleEl) {
          titleEl.appendChild(pill);
        } else {
          parent.insertBefore(pill, nameEl.nextSibling);
        }
      }
    } catch (e) {
      console.warn('vol-tag error:', e);
    }
  }

  // 等待 DOM 就绪 + 卡片渲染完成（a-rolling-app.js 可能动态创建卡片）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(loadTags, 800));
  } else {
    setTimeout(loadTags, 800);
  }
  // 也监听卡片刷新（a-rolling-app.js 的 refresh 事件）
  document.addEventListener('rolling-refresh', () => { loaded = false; setTimeout(loadTags, 1000); });
})();