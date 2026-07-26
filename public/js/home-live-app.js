/**
 * Home page A-share + US live price overlays.
 * Both markets refresh every 15 seconds only during their active sessions.
 */
(function () {
  const adapter = window.EtfQuote;
  if (!adapter?.aShareSymbolsParam || !adapter?.normalizeQuotePayload || !adapter?.quoteMapByCode) return;

  const aCodes = Array.from(document.querySelectorAll('[data-code]')).map((el) => el.dataset.code).filter(Boolean);
  const usSymbols = Array.from(document.querySelectorAll('[data-us-home-symbol]')).map((el) => el.dataset.usHomeSymbol).filter(Boolean);
  const status = document.getElementById('home-live-status');
  let aLoading = false;
  let usLoading = false;

  const paintStatus = (market, text) => {
    if (!status) return;
    status.dataset[market] = text;
    status.textContent = [
      status.dataset.a && `A股 ${status.dataset.a}`,
      status.dataset.us && `美股 ${status.dataset.us}`,
    ].filter(Boolean).join('｜');
  };

  async function loadA() {
    if (aLoading || document.hidden || aCodes.length === 0) return;
    aLoading = true;
    try {
      const symbols = adapter.aShareSymbolsParam(aCodes);
      const res = await fetch(`/api/public/v1/quote?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) return;
      const normalized = adapter.normalizeQuotePayload(await res.json());
      if (!normalized.ok) return;
      const map = adapter.quoteMapByCode(normalized);
      aCodes.forEach((code) => {
        const quote = map.get(String(code));
        if (!quote || !(Number(quote.price) > 0)) return;
        const priceEl = document.getElementById(`home-live-price-${code}`);
        const changeEl = document.getElementById(`home-live-change-${code}`);
        const changePct = Number(quote.change_percent ?? quote.change_pct);
        if (priceEl) priceEl.textContent = `¥${Number(quote.price).toFixed(Number(quote.price) >= 10 ? 2 : 3)}`;
        if (changeEl && Number.isFinite(changePct)) {
          changeEl.textContent = `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`;
          changeEl.style.color = changePct > 0 ? '#cf1322' : changePct < 0 ? '#389e0d' : '#64748b';
        }
      });
    } finally { aLoading = false; }
  }

  async function loadUs() {
    if (usLoading || document.hidden || usSymbols.length === 0) return;
    usLoading = true;
    try {
      const res = await fetch(`/api/public/v1/quote?symbols=${encodeURIComponent(usSymbols.join(','))}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) return;
      const normalized = adapter.normalizeQuotePayload(await res.json());
      if (!normalized.ok) return;
      const map = new Map(normalized.items.map((item) => [String(item.code || item.symbol).split('.')[0].toUpperCase(), item]));
      usSymbols.forEach((symbol) => {
        const quote = map.get(String(symbol).toUpperCase());
        if (!quote || !(Number(quote.price) > 0)) return;
        const priceEl = document.getElementById(`home-us-live-price-${symbol}`);
        const changeEl = document.getElementById(`home-us-live-change-${symbol}`);
        const changePct = Number(quote.change_percent ?? quote.change_pct);
        if (priceEl) priceEl.textContent = `$${Number(quote.price).toFixed(2)}`;
        if (changeEl && Number.isFinite(changePct)) {
          changeEl.textContent = `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`;
          changeEl.style.color = changePct > 0 ? '#cf1322' : changePct < 0 ? '#389e0d' : '#64748b';
        }
      });
    } finally { usLoading = false; }
  }

  if (window.EtfLivePoll?.startMarketPoll) {
    window.EtfLivePoll.startMarketPoll({ market: 'CN_A', intervalMs: 15_000, tick: loadA, onStatus: (text) => paintStatus('a', text) });
    window.EtfLivePoll.startMarketPoll({ market: 'US', intervalMs: 15_000, tick: loadUs, onStatus: (text) => paintStatus('us', text) });
  }
})();
