(() => {
  const DATA_URL = '/data/precious-inventory.json';

  const fmtT = (t) => {
    const n = Number(t) || 0;
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(2)}k`;
    return n.toFixed(n < 10 ? 2 : 0);
  };
  const fmtInt = (n) => (Number(n) || 0).toLocaleString('en-US');
  const sign = (n) => (n > 0 ? '+' : n < 0 ? '−' : '');

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
    const prev = (d.recent && d.recent.length > 1) ? d.recent[d.recent.length - 2] : null;
    const chg = prev ? +(l.value - prev.value).toFixed(3) : 0;
    const chgCls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
    setTag('fred-tag', `更新 ${l.date}`);
    document.getElementById('fred-big').innerHTML = `
      <strong>${l.value.toFixed(2)}%</strong>
      <small>${l.date}</small>
      <span class="rate-change ${chgCls}">${sign(chg)}${Math.abs(chg).toFixed(3)}%</span>`;
    document.getElementById('fred-rows').innerHTML = (d.recent || []).slice(-8).reverse().map(r => `
      <div class="rate-row"><span>${r.date}</span><strong>${r.value.toFixed(2)}%</strong></div>`).join('');
    setStatus('fred-status', `FRED DFII10 · 10年期TIPS实际收益率，%`);
  };

  // ── Kitco ────────────────────────────────────────────
  const renderKitco = (d) => {
    if (!d.ok) { setTag('kitco-tag', '不可用', 'down'); setStatus('kitco-status', '数据暂不可用'); return; }
    if (d.goldRate != null || d.silverRate != null) {
      setTag('kitco-tag', '当日');
      document.getElementById('kitco-big').innerHTML = `
        <strong>${(d.goldRate ?? '—')}%</strong>
        <small>黄金租借</small>`;
      document.getElementById('kitco-rows').innerHTML = [
        `<div class="rate-row"><span>黄金 1M</span><strong>${d.goldRate != null ? d.goldRate.toFixed(2) : '—'}%</strong></div>`,
        `<div class="rate-row"><span>白银 1M</span><strong>${d.silverRate != null ? d.silverRate.toFixed(2) : '—'}%</strong></div>`,
      ].join('');
      setStatus('kitco-status', `Kitco Lease Rates`);
    } else {
      setTag('kitco-tag', '已连接');
      setStatus('kitco-status', `租赁利率解析待完善（页面 ${d.bodyLength || 0}B）`);
      document.getElementById('kitco-rows').innerHTML = `<div class="empty">暂无解析数据</div>`;
    }
  };

  // ── 主加载 ───────────────────────────────────────────
  (async () => {
    try {
      const resp = await fetch(DATA_URL);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const d = await resp.json();
      if (d.status !== 'ok') throw new Error(d.error || 'fetch failed');
      const data = d.data || {};
      renderShfe(data.shfe);
      renderLbma(data.lbma);
      renderCme(data.cme);
      renderFred(data.fred);
      renderKitco(data.kitco);
    } catch (e) {
      const els = ['shfe', 'lbma', 'cme', 'fred', 'kitco'];
      els.forEach(k => {
        setTag(`${k}-tag`, '加载失败', 'down');
        setStatus(`${k}-status`, '网络错误或接口不可用');
      });
      console.error('precious-inventory load failed:', e);
    }
  })();
})();