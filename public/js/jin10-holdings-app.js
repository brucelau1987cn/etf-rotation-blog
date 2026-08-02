(() => {
  const ETF_API = '/api/public/v1/jin10-etf-reports';
  const panels = [
    { attr: 1, tag: 'gold-tag', latest: 'gold-latest', status: 'gold-status', list: 'gold-list', name: '黄金ETF' },
    { attr: 2, tag: 'silver-tag', latest: 'silver-latest', status: 'silver-status', list: 'silver-list', name: '白银ETF' },
  ];

  const maxVal = (rows) => Math.max(1, ...rows.map((r) => Math.abs(r.inc_trust || 0) + Math.abs(r.dec_trust || 0)));

  const fmtMoney = (v) => {
    const n = Number(v) || 0;
    if (n >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
    if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
    return n.toFixed(0);
  };

  const renderPanel = (panel, d) => {
    const rows = d.rows || [];
    const tag = document.getElementById(panel.tag);
    const latestEl = document.getElementById(panel.latest);
    const statusEl = document.getElementById(panel.status);
    const listEl = document.getElementById(panel.list);

    const latest = d.latest || {};
    const latestNet = (latest.inc_trust || 0) - (latest.dec_trust || 0);
    tag.textContent = `最新净 ${latestNet >= 0 ? '+' : ''}${latestNet.toFixed(2)}t`;
    tag.className = `asset-tag ${latestNet >= 0 ? 'up' : 'down'}`;

    latestEl.innerHTML = [
      `<div class="latest-cell"><span>报告周</span><strong>${latest.reported_on || '—'}</strong></div>`,
      `<div class="latest-cell"><span>增持</span><strong class="up">${(latest.inc_trust || 0).toFixed(2)}t</strong></div>`,
      `<div class="latest-cell"><span>减持</span><strong class="down">${(latest.dec_trust || 0).toFixed(2)}t</strong></div>`,
      `<div class="latest-cell"><span>增减市值</span><strong>${fmtMoney((latest.inc_value || 0) - (latest.dec_value || 0))}</strong></div>`,
    ].join('');

    statusEl.textContent = `近12周 · 净变化 ${d.net_trust >= 0 ? '+' : ''}${d.net_trust}t · 增持市值 ${fmtMoney(d.inc_value_total)} · 减持市值 ${fmtMoney(d.dec_value_total)}`;

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">暂无持仓数据</div>';
      return;
    }
    const max = maxVal(rows);
    listEl.innerHTML = `<div class="holdings-header"><span>日期</span><span>增持↑ / 减持↓</span><span>净变化</span></div>${rows.map((r) => {
      const inc = Math.abs(r.inc_trust || 0);
      const dec = Math.abs(r.dec_trust || 0);
      const net = (r.inc_trust || 0) - (r.dec_trust || 0);
      const incPct = (inc / max) * 100;
      const decPct = (dec / max) * 100;
      return `<div class="holdings-row">
        <div class="holdings-week">${r.reported_on}</div>
        <div class="holdings-bars">
          ${inc > 0 ? `<div class="holdings-bar inc" style="width:${incPct.toFixed(1)}%"><span>${inc.toFixed(2)}</span></div>` : ''}
          ${dec > 0 ? `<div class="holdings-bar dec" style="width:${decPct.toFixed(1)}%"><span>${dec.toFixed(2)}</span></div>` : ''}
        </div>
        <div class="holdings-net ${net >= 0 ? 'up' : 'down'}">${net > 0 ? '+' : ''}${net.toFixed(2)}t</div>
      </div>`;
    }).join('')}`;
  };

  const load = async (panel) => {
    const statusEl = document.getElementById(panel.status);
    statusEl.textContent = '加载中…';
    try {
      const resp = await fetch(`${ETF_API}?attr_id=${panel.attr}&weeks=12`);
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