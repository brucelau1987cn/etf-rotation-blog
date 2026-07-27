(function (root) {
  function summarize(payloads, total) {
    const list = Array.isArray(payloads) ? payloads : [];
    const configured = Math.max(0, Number(total) || 0);
    const live = list.filter(item => item?.delivery?.state === 'live' && item?.freshness === 'fresh').length;
    const lkg = list.filter(item => item?.delivery?.state === 'lkg' || item?.freshness === 'stale').length;
    const unavailable = Math.max(0, configured - list.length);
    if (configured > 0 && live === configured) {
      return { state: 'live', text: `● ${live}/${configured} 标的实时同步` };
    }
    const degraded = [lkg ? `${lkg}个LKG` : '', unavailable ? `${unavailable}个不可用` : ''].filter(Boolean).join(' · ');
    if (live > 0) {
      return { state: 'partial', text: `⚠ ${live}/${configured} 实时${degraded ? ` · ${degraded}` : ''}` };
    }
    return { state: 'lkg', text: `⚠ 0/${configured} 实时${degraded ? ` · ${degraded}` : ' · 静态快照'}` };
  }

  root.ARollingDelivery = { summarize };
})(typeof window !== 'undefined' ? window : globalThis);
