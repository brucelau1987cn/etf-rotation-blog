(() => {
  const DATA_URL = '/data/precious-inventory.json';
  const API_URL = '/api/public/v1/implied-lease-rate';

  const fmtT = (t) => {
    const n = Number(t) || 0;
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(2)}k`;
    return n.toFixed(n < 10 ? 2 : 0);
  };
  const fmtInt = (n) => (Number(n) || 0).toLocaleString('en-US');
  const sign = (n) => (n > 0 ? '+' : n < 0 ? '−' : '');
  const fmtPct = (n, digits = 2) => {
    if (n == null || Number.isNaN(Number(n))) return '—';
    return `${Number(n).toFixed(digits)}%`;
  };

  const setTag = (id, text, cls) => {
    const el = document.getElementById(id);
    if (el) { el.textContent = text; el.className = `asset-tag ${cls || ''}`; }
  };
  const setStatus = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };

  // ── SHFE ─────────────────────────────────────────────
  const renderShfe = (d) => {
    if (!d.ok) { setTag('shfe-tag', '不可用', 'down'); setStatus('shfe-status', `数据暂不可用：${d.error || '接口错误'}`); return; }
    setTag('shfe-tag', `更新 ${d.reportDate}`);
    const g = d.gold, s = d.silver;
    document.getElementById('shfe-kpis').innerHTML = [
      `<div class="inv-kpi"><span>黄金仓单</span><strong>${fmtInt(g.kg)}<small>kg</small></strong></div>`,
      `<div class="inv-kpi"><span>黄金</span><strong>${(g.tonnes || 0).toFixed(2)}<small>t</small></strong></div>`,
      `<div class="inv-kpi"><span>白银仓单</span><strong>${fmtInt(s.kg)}<small>kg</small></strong></div>`,
      `<div class="inv-kpi"><span>白银</span><strong>${(s.tonnes || 0).toFixed(2)}<small>t</small></strong></div>`,
    ].join('');
    setStatus('shfe-status', `${d.note || ''} · 数据日期 ${d.reportDate}`);
  };

  // ── LBMA ─────────────────────────────────────────────
  const renderLbma = (d) => {
    if (!d.ok || !d.latest) { setTag('lbma-tag', '不可用', 'down'); setStatus('lbma-status', '数据暂不可用'); return; }
    const l = d.latest, chg = d.change || {};
    setTag('lbma-tag', `更新 ${l.date}`);
    document.getElementById('lbma-kpis').innerHTML = [
      `<div class="inv-kpi"><span>黄金库存</span><strong>${fmtInt(l.goldT)}<small>t</small></strong></div>`,
      `<div class="inv-kpi"><span>黄金月变</span><strong class="${chg.goldT >= 0 ? 'up' : 'down'}">${sign(chg.goldT)}${fmtInt(Math.abs(chg.goldT))}<small>t</small></strong></div>`,
      `<div class="inv-kpi"><span>白银库存</span><strong>${fmtInt(l.silverT)}<small>t</small></strong></div>`,
      `<div class="inv-kpi"><span>白银月变</span><strong class="${chg.silverT >= 0 ? 'up' : 'down'}">${sign(chg.silverT)}${fmtInt(Math.abs(chg.silverT))}<small>t</small></strong></div>`,
    ].join('');
    setStatus('lbma-status', `月更 · 数据 ${l.date} · 共 ${d.total} 期`);
  };

  // ── CME ──────────────────────────────────────────────
  const renderCme = (d) => {
    if (!d.ok) { setTag('cme-tag', '不可用', 'down'); setStatus('cme-status', `CME 数据暂不可用`); return; }
    const g = d.gold, s = d.silver;
    setTag('cme-tag', `更新 ${d.date || ''}`);
    document.getElementById('cme-kpis').innerHTML = [
      `<div class="inv-kpi"><span>黄金 Registered</span><strong>${g.registered}<small>oz</small></strong></div>`,
      `<div class="inv-kpi"><span>黄金 Eligible</span><strong>${g.eligible}<small>oz</small></strong></div>`,
      `<div class="inv-kpi"><span>白银 Registered</span><strong>${s.registered}<small>oz</small></strong></div>`,
      `<div class="inv-kpi"><span>白银 Eligible</span><strong>${s.eligible}<small>oz</small></strong></div>`,
    ].join('');
    setStatus('cme-status', `${d.note || ''} · 数据 ${d.date || ''}`);
  };

  // ── FRED ─────────────────────────────────────────────
  const renderFred = (d) => {
    if (!d.ok || !d.latest) { setTag('fred-tag', '不可用', 'down'); setStatus('fred-status', '数据暂不可用'); return; }
    const l = d.latest;
    const prev = (d.recent && d.recent.length > 1) ? d.recent[1] : null;
    const chg = prev ? +(l.value - prev.value).toFixed(3) : 0;
    const chgCls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
    setTag('fred-tag', `更新 ${l.date}`);
    document.getElementById('fred-big').innerHTML = `
      <strong>${l.value.toFixed(2)}%</strong>
      <small>${l.date}</small>
      <span class="rate-change ${chgCls}">${sign(chg)}${Math.abs(chg).toFixed(3)}%</span>`;
    document.getElementById('fred-rows').innerHTML = (d.recent || []).slice(0, 8).map(r => `
      <div class="rate-row"><span>${r.date}</span><strong>${r.value.toFixed(2)}%</strong></div>`).join('');
    setStatus('fred-status', `Treasury TIPS · 10年期实际收益率，%`);
  };

  // ── Implied lease ────────────────────────────────────
  const tenorRate = (metal, tenor) => {
    if (!metal) return null;
    const key = ({ '1M': 'rate_1m', '3M': 'rate_3m', '6M': 'rate_6m', '1Y': 'rate_1y' })[tenor];
    if (key && metal[key] != null) return metal[key];
    const hit = (metal.tenors || []).find((t) => t.tenor === tenor);
    return hit ? hit.rate : null;
  };

  const renderLease = (d, sourceLabel = '静态') => {
    if (!d || !d.ok) {
      setTag('kitco-tag', '不可用', 'down');
      setStatus('kitco-status', `隐含租赁利率暂不可用${d && d.error ? `：${d.error}` : ''}`);
      document.getElementById('kitco-big').innerHTML = '';
      document.getElementById('kitco-rows').innerHTML = `<div class="empty">暂无解析数据</div>`;
      return;
    }
    const g = d.gold || {};
    const s = d.silver || {};
    const headline = d.headline_rate != null ? d.headline_rate : tenorRate(g, '1M');
    const asOf = d.as_of || (d.usd_curve && d.usd_curve.date) || '';
    setTag('kitco-tag', asOf ? `更新 ${asOf}` : '估算');
    document.getElementById('kitco-big').innerHTML = `
      <strong>${fmtPct(headline)}</strong>
      <small>黄金 1M 隐含</small>`;
    const rows = [
      ['黄金 1M', tenorRate(g, '1M')],
      ['黄金 3M', tenorRate(g, '3M')],
      ['黄金 6M', tenorRate(g, '6M')],
      ['黄金 1Y', tenorRate(g, '1Y')],
      ['白银 1M', tenorRate(s, '1M')],
      ['白银 3M', tenorRate(s, '3M')],
      ['白银 6M', tenorRate(s, '6M')],
      ['白银 1Y', tenorRate(s, '1Y')],
    ];
    document.getElementById('kitco-rows').innerHTML = rows.map(([label, val]) => `
      <div class="rate-row"><span>${label}</span><strong>${fmtPct(val)}</strong></div>`).join('');
    const method = d.method || 'comex_forward_proxy';
    setStatus(
      'kitco-status',
      `隐含租赁利率 · ${method} · ${sourceLabel}${d.note ? ` · ${d.note}` : ''}`,
    );
  };

  const fetchLeaseLive = async () => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    try {
      const resp = await fetch(`${API_URL}?bust=${Date.now()}`, {
        signal: ctrl.signal,
        headers: { Accept: 'application/json' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await resp.json();
      if (body.status === 'ok' && body.data) return body.data;
      if (body.ok) return body;
      throw new Error(body.error || 'api failed');
    } finally {
      clearTimeout(timer);
    }
  };

  // ── 主加载 ───────────────────────────────────────────
  (async () => {
    try {
      const resp = await fetch(`${DATA_URL}?bust=${Date.now()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const d = await resp.json();
      if (d.status !== 'ok') throw new Error(d.error || 'fetch failed');
      const data = d.data || {};
      renderShfe(data.shfe || {});
      renderLbma(data.lbma || {});
      renderCme(data.cme || {});
      renderFred(data.fred || {});

      const staticLease = data.implied_lease || data.kitco || {};
      renderLease(staticLease, '静态JSON');

      // Prefer live CF edge calc when available; fall back silently to static.
      try {
        const live = await fetchLeaseLive();
        if (live && live.ok) renderLease(live, 'CF在线');
      } catch (liveErr) {
        console.warn('implied-lease live api unavailable, using static:', liveErr);
      }
    } catch (e) {
      const els = ['shfe', 'lbma', 'cme', 'fred', 'kitco'];
      els.forEach((k) => {
        setTag(`${k}-tag`, '加载失败', 'down');
        setStatus(`${k}-status`, '网络错误或接口不可用');
      });
      console.error('precious-inventory load failed:', e);
    }
  })();
})();
