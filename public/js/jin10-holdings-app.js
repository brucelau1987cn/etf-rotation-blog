(() => {
  const ETF_API = '/api/public/v1/jin10-etf-reports';
  const LIMIT = 15;
  const panels = [
    { attr: 1, tag: 'gold-tag', latest: 'gold-latest', status: 'gold-status', list: 'gold-list', chart: 'gold-chart', name: '黄金ETF' },
    { attr: 2, tag: 'silver-tag', latest: 'silver-latest', status: 'silver-status', list: 'silver-list', chart: 'silver-chart', name: '白银ETF' },
  ];

  const maxAbs = (rows) => Math.max(0.01, ...rows.map((r) => Math.abs(r.change || 0)));

  const fmtMoney = (v) => {
    const n = Number(v) || 0;
    if (n >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
    if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
    return n.toFixed(0);
  };

  // SVG line chart of total trust over time (newest first).
  const renderChart = (rows) => {
    if (!rows || rows.length < 2) return '';
    const W = 560;
    const H = 120;
    const PAD = 8;
    const ordered = [...rows].reverse(); // oldest → newest for the line
    const minTrust = Math.min(...ordered.map((r) => r.trust || 0));
    const maxTrust = Math.max(...ordered.map((r) => r.trust || 0));
    const span = Math.max(0.001, maxTrust - minTrust);
    const xAt = (i) => PAD + (i * (W - PAD * 2)) / Math.max(1, ordered.length - 1);
    const yAt = (v) => H - PAD - ((v - minTrust) / span) * (H - PAD * 2);
    const pts = ordered.map((r, i) => `${xAt(i).toFixed(1)},${yAt(r.trust || 0).toFixed(1)}`);
    const area = `M${pts[0]} L${pts.join(' L')} L${xAt(ordered.length - 1).toFixed(1)},${(H - PAD).toFixed(1)} L${PAD.toFixed(1)},${(H - PAD).toFixed(1)} Z`;
    return `<svg class="trust-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="总持仓走势">
      <defs><linearGradient id="trust-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#f59e0b" stop-opacity=".22"/>
        <stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#trust-fill)"/>
      <polyline points="${pts.join(' ')}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${ordered.map((r, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(r.trust || 0).toFixed(1)}" r="2.2" fill="#fff" stroke="#f59e0b" stroke-width="1.4"><title>${r.reported_on} · ${(r.trust || 0).toFixed(1)}t</title></circle>`).join('')}
    </svg>`;
  };

  const renderPanel = (panel, d) => {
    const rows = d.rows || [];
    const tag = document.getElementById(panel.tag);
    const latestEl = document.getElementById(panel.latest);
    const statusEl = document.getElementById(panel.status);
    const listEl = document.getElementById(panel.list);
    const chartEl = document.getElementById(panel.chart);

    const latest = d.latest || {};
    const latestChange = latest.change || 0;
    tag.textContent = `最新 ${latestChange >= 0 ? '+' : ''}${latestChange.toFixed(2)}t`;
    tag.className = `asset-tag ${latestChange >= 0 ? 'up' : 'down'}`;

    latestEl.innerHTML = [
      `<div class="latest-cell"><span>日期</span><strong>${latest.reported_on || '—'}</strong></div>`,
      `<div class="latest-cell"><span>总持仓</span><strong>${(latest.trust || 0).toFixed(1)}t</strong></div>`,
      `<div class="latest-cell"><span>当日变化</span><strong class="${latestChange >= 0 ? 'up' : 'down'}">${latestChange >= 0 ? '+' : ''}${latestChange.toFixed(2)}t</strong></div>`,
      `<div class="latest-cell"><span>总市值</span><strong>${fmtMoney(latest.value)}</strong></div>`,
    ].join('');

    statusEl.textContent = `近${d.limit || LIMIT}天 · 净变化 ${d.net_change >= 0 ? '+' : ''}${d.net_change}t`;
    chartEl.innerHTML = renderChart(rows);

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">暂无持仓数据</div>';
      return;
    }
    const max = maxAbs(rows);
    listEl.innerHTML = `<div class="holdings-header"><span>日期</span><span>变化（吨）</span><span>净变化</span></div>${rows.map((r) => {
      const change = r.change || 0;
      const inc = change > 0 ? change : 0;
      const dec = change < 0 ? Math.abs(change) : 0;
      const incPct = (inc / max) * 100;
      const decPct = (dec / max) * 100;
      return `<div class="holdings-row">
        <div class="holdings-week">${r.reported_on}</div>
        <div class="holdings-bars">
          ${inc > 0 ? `<div class="holdings-bar inc" style="width:${incPct.toFixed(1)}%"><span>+${inc.toFixed(2)}</span></div>` : ''}
          ${dec > 0 ? `<div class="holdings-bar dec" style="width:${decPct.toFixed(1)}%"><span>−${dec.toFixed(2)}</span></div>` : ''}
          ${inc === 0 && dec === 0 ? '<span class="empty" style="color:rgb(var(--gray));padding:0 .5rem">0</span>' : ''}
        </div>
        <div class="holdings-net ${change >= 0 ? 'up' : 'down'}">${change >= 0 ? '+' : ''}${change.toFixed(2)}t</div>
      </div>`;
    }).join('')}`;
  };

  const load = async (panel) => {
    const statusEl = document.getElementById(panel.status);
    statusEl.textContent = '加载中…';
    try {
      const resp = await fetch(`${ETF_API}?attr_id=${panel.attr}&limit=${LIMIT}`);
      const d = await resp.json();
      if (d.status !== 'ok') throw new Error(d.error || 'fetch failed');
      renderPanel(panel, d);
    } catch (e) {
      statusEl.textContent = '加载失败';
      document.getElementById(panel.list).innerHTML = '<div class="empty">数据暂时不可用</div>';
    }
  };

  panels.forEach(load);
})();