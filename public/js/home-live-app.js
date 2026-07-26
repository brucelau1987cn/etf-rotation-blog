/**
 * Home page live price overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
(function () {
  const adapter = window.EtfQuote;
  if (!adapter?.aShareSymbolsParam || !adapter?.normalizeQuotePayload || !adapter?.quoteMapByCode) {
    return;
  }

  const codes = Array.from(document.querySelectorAll('[data-code]'))
    .map((el) => el.dataset.code)
    .filter(Boolean);
  if (codes.length === 0) return;

  let loading = false;

  async function loadHomeLive() {
    if (loading || document.hidden) return;
    loading = true;
    try {
      const symbols = adapter.aShareSymbolsParam(codes);
      const res = await fetch(
        `/api/public/v1/quote?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`,
        { cache: 'no-store' },
      );
      if (!res.ok) return;
      const data = await res.json();
      const normalized = adapter.normalizeQuotePayload(data);
      if (!normalized.ok) return;
      const map = adapter.quoteMapByCode(normalized);
      codes.forEach((code) => {
        const quote = map.get(String(code));
        if (!quote || typeof quote.price !== 'number' || !(quote.price > 0)) return;
        const priceEl = document.getElementById(`home-live-price-${code}`);
        const changeEl = document.getElementById(`home-live-change-${code}`);
        const changePct = typeof quote.change_percent === 'number' ? quote.change_percent : quote.change_pct;
        if (priceEl) priceEl.textContent = `¥${quote.price.toFixed(quote.price >= 10 ? 2 : 3)}`;
        if (changeEl && typeof changePct === 'number') {
          const isUp = changePct > 0;
          const isDown = changePct < 0;
          const sign = isUp ? '+' : '';
          changeEl.textContent = `${sign}${changePct.toFixed(2)}%`;
          changeEl.style.color = isUp ? '#cf1322' : isDown ? '#389e0d' : '#64748b';
        }
      });
    } catch (_) {
      // keep last painted values
    } finally {
      loading = false;
    }
  }

  if (window.EtfLivePoll?.startLivePoll) {
    window.EtfLivePoll.startLivePoll({
      intervalMs: 30_000,
      immediate: true,
      tick: loadHomeLive,
    });
  } else {
    void loadHomeLive();
    window.setInterval(loadHomeLive, 30_000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) void loadHomeLive();
    });
  }
})();
