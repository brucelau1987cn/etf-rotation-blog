/**
 * 量价 12 态标签：遍历 .instrument-board 卡片，请求 price-volume-tag 端点，
 * 在每张卡片标题旁插入量价关系标签 pill。
 * 排除低筹码页（不使用 ARollingEnergyMatrix 组件，天然不受影响）。
 */
(function () {
  const EXCH_MAP = { SSE: '17', SZSE: '33' };
  let loaded = false;

  async function loadTags() {
    if (loaded) return;
    loaded = true;
    const boards = document.querySelectorAll('.instrument-board');
    if (!boards.length) return;

    // 收集 symbols（A股：exchange→market code；期货：原样）
    const symbols = [];
    const boardMap = new Map(); // rawSymbol -> DOM element
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
    if (!symbols.length) return;

    try {
      const resp = await fetch(`/api/public/v1/price-volume-tag?s=${symbols.join(',')}&_=${Date.now()}`, { credentials: 'omit' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.ok || !data.tags) return;

      for (const [raw, tag] of Object.entries(data.tags)) {
        if (!tag.ok) continue;
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