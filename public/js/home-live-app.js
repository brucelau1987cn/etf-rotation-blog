/**
 * Home page live price overlay client.
 * Depends on /js/normalize-quote-payload.js and /js/etf-live-poll.js.
 */
(function() {
  const adapter = window.EtfQuote;
  if (!adapter) return;
  const codes = Array.from(document.querySelectorAll('[data-code]')).map(el => el.dataset.code).filter(Boolean);
  if (codes.length === 0) return;
  const symbols = adapter.aShareSymbolsParam(codes);
  fetch(`/api/public/v1/quote?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' })
    .then(res => res.ok ? res.json() : null)
    .then(data => {
      const normalized = adapter.normalizeQuotePayload(data);
      if (!normalized.ok) return;
      const map = adapter.quoteMapByCode(normalized);
      codes.forEach(code => {
        const quote = map.get(String(code));
        if (!quote || typeof quote.price !== 'number') return;
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
    })
    .catch(() => {});
})();
