;(function () {
  'use strict';

  var cards = Array.from(document.querySelectorAll('.tc-card[data-live-symbol][data-tracking-status="active"]'));
  if (!cards.length) return;

  var POLL_MS = 30000;
  var UP = '#e04444';
  var DOWN = '#0aa869';
  var quoteBySymbol = new Map();
  var fetching = false;
  var currentPhase = null;

  function parseQuoteDate(value) {
    var text = String(value || '').trim();
    if (!text) return null;
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/.test(text)) {
      text = text.replace(' ', 'T') + '+08:00';
    }
    var date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function shanghaiParts(value) {
    var date = value == null ? new Date() : parseQuoteDate(value);
    if (!date) return null;
    var parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(date).filter(function (part) { return part.type !== 'literal'; }).map(function (part) {
      return [part.type, part.value];
    }));
    return {
      date: parts.year + '-' + parts.month + '-' + parts.day,
      time: parts.hour + ':' + parts.minute,
      hhmm: Number(parts.hour + parts.minute)
    };
  }

  function quoteParts(quote) {
    return shanghaiParts(quote && quote.quote_time);
  }

  function fmt(value, digits) {
    var number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits == null ? 2 : digits) : '—';
  }

  function signed(value, digits) {
    var number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return (number >= 0 ? '+' : '') + number.toFixed(digits == null ? 2 : digits);
  }

  function liveMode(quote) {
    var now = shanghaiParts();
    var quoted = quoteParts(quote);
    if (!now || !quoted) return '行情时间待确认';
    if (quoted.date !== now.date) return '最近行情 ' + quoted.date;
    if (currentPhase) {
      if (currentPhase.active) return '盘中';
      if (currentPhase.label === '午间休市') return '午间休市';
      if (currentPhase.label === '已收盘') return '收盘待结算';
      if (currentPhase.label === '今日休市') return '最近行情 ' + quoted.date;
    }
    return now.hhmm <= 1505 ? '盘中' : '收盘待结算';
  }

  function setDirection(element, value) {
    if (!element) return;
    var valid = Number.isFinite(value);
    element.classList.toggle('tc-up', valid && value > 0);
    element.classList.toggle('tc-down', valid && value < 0);
  }

  function drawLivePoint(card, price, totalChange) {
    var chart = card.querySelector('.tc-price-chart');
    var svg = chart && chart.querySelector('svg');
    if (!chart || !svg) return;

    var closes;
    try { closes = JSON.parse(chart.dataset.priceCloses || '[]').map(Number).filter(function (value) { return Number.isFinite(value) && value > 0; }); }
    catch (_) { closes = []; }
    if (!closes.length) {
      var anchor = Number(chart.dataset.chartAnchor);
      if (Number.isFinite(anchor) && anchor > 0) closes = [anchor];
    }
    if (!closes.length || !Number.isFinite(price)) return;

    var values = closes.concat([price]);
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var range = max - min || 1;
    var pad = 4;
    var width = 220;
    var height = 56;
    var points = values.map(function (value, index) {
      return {
        x: pad + (index / (values.length - 1)) * (width - pad * 2),
        y: height - pad - ((value - min) / range) * (height - pad * 2)
      };
    });
    var staticPoints = points.slice(0, -1);
    var lastStatic = staticPoints[staticPoints.length - 1];
    var live = points[points.length - 1];
    var color = totalChange >= 0 ? UP : DOWN;
    var pointText = staticPoints.map(function (point) { return point.x.toFixed(1) + ',' + point.y.toFixed(1); }).join(' ');

    var line = svg.querySelector('.tc-price-line');
    var area = svg.querySelector('.tc-price-area');
    var point = svg.querySelector('.tc-price-point');
    var connector = svg.querySelector('.tc-live-connector');
    var livePoint = svg.querySelector('.tc-live-point');
    var label = svg.querySelector('.tc-live-label');
    if (!line || !area || !point || !connector || !livePoint || !label) return;

    line.setAttribute('points', pointText);
    line.setAttribute('stroke', color);
    area.setAttribute('d', 'M' + staticPoints[0].x.toFixed(1) + ',' + height + ' L' + pointText.replace(/ /g, ' L') + ' L' + lastStatic.x.toFixed(1) + ',' + height + ' Z');
    area.setAttribute('fill', color + '1a');
    point.setAttribute('cx', lastStatic.x.toFixed(1));
    point.setAttribute('cy', lastStatic.y.toFixed(1));
    point.setAttribute('fill', color);
    connector.setAttribute('x1', lastStatic.x.toFixed(1));
    connector.setAttribute('y1', lastStatic.y.toFixed(1));
    connector.setAttribute('x2', live.x.toFixed(1));
    connector.setAttribute('y2', live.y.toFixed(1));
    connector.setAttribute('stroke', color);
    connector.removeAttribute('hidden');
    livePoint.setAttribute('cx', live.x.toFixed(1));
    livePoint.setAttribute('cy', live.y.toFixed(1));
    livePoint.setAttribute('stroke', color);
    livePoint.removeAttribute('hidden');
    label.setAttribute('x', String(width - pad));
    label.setAttribute('y', String(Math.max(9, live.y - 5).toFixed(1)));
    label.setAttribute('fill', color);
    label.removeAttribute('hidden');

    var rangeLabel = chart.querySelector('.tc-chart-title em');
    if (rangeLabel) rangeLabel.textContent = '最高 ¥' + fmt(max) + ' / 最低 ¥' + fmt(min);
  }

  function appendCell(row, text, className) {
    var cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = text;
    row.appendChild(cell);
  }

  function renderLiveRow(card, quote, mode, dailyChange) {
    var body = card.querySelector('[data-live-table-body]');
    if (!body) return;
    var old = body.querySelector('.tc-live-row');
    if (old) old.remove();
    var quoted = quoteParts(quote);
    if (!quoted) return;
    var row = document.createElement('tr');
    row.className = 'tc-live-row';
    appendCell(row, quoted.date.slice(5) + ' ' + (mode === '盘中' ? '今日盘中' : mode), 'tc-date');
    appendCell(row, '¥' + fmt(quote.price));
    appendCell(row, Number.isFinite(dailyChange) ? signed(dailyChange) + '%' : '—', Number.isFinite(dailyChange) ? (dailyChange > 0 ? 'tc-up' : dailyChange < 0 ? 'tc-down' : '') : '');
    appendCell(row, '获利盘待收盘');
    body.appendChild(row);

    var details = body.closest('.tc-table-details');
    var summary = details && details.querySelector('summary');
    if (summary) summary.textContent = '每日明细（' + (details.dataset.settledDays || '0') + ' 个收盘日 + 今日行情）';
  }

  function validQuote(quote) {
    if (!quote || quote.status !== 'ok' || quote.price == null) return false;
    var price = Number(quote.price);
    var quoted = quoteParts(quote);
    return Number.isFinite(price) && price > 0 && Boolean(quoted && quoted.date && quoted.time);
  }

  function renderCard(card, quote) {
    if (!validQuote(quote)) return false;
    var price = Number(quote.price);
    var quoted = quoteParts(quote);
    var firstClose = card.dataset.firstClose === '' ? NaN : Number(card.dataset.firstClose);
    if (!quoted || quoted.date <= (card.dataset.latestDate || '')) return false;

    var dailyChange = quote.change_percent == null ? NaN : Number(quote.change_percent);
    if (!Number.isFinite(dailyChange)) dailyChange = NaN;
    var hasFormalDay = Number.isFinite(firstClose) && firstClose > 0;
    var liveBaseline = Number.isFinite(firstClose) ? firstClose : price;
    var totalChange = hasFormalDay ? ((price - liveBaseline) / liveBaseline) * 100 : 0;
    var mode = liveMode(quote);
    var change = card.querySelector('[data-live-change]');
    var summary = card.querySelector('[data-live-summary]');
    var badge = card.querySelector('[data-live-badge]');

    if (change) {
      change.textContent = '实时 ¥' + fmt(price) + '  ' + (Number.isFinite(dailyChange) ? signed(dailyChange) + '%' : '—');
      setDirection(change, dailyChange);
      change.title = '行情时间 ' + quoted.time;
    }
    if (summary) {
      summary.textContent = hasFormalDay
        ? '加入以来：' + signed(totalChange) + '% · ¥' + fmt(liveBaseline) + ' → ¥' + fmt(price)
        : '加入以来：第1日待结算';
    }
    if (badge) {
      badge.textContent = mode + ' · ' + quoted.time;
      badge.hidden = false;
    }
    card.dataset.totalChange = String(totalChange);
    card.dataset.liveApplied = '1';
    quoteBySymbol.set(card.dataset.liveSymbol, { totalChange: totalChange, quote: quote });
    drawLivePoint(card, price, totalChange);
    renderLiveRow(card, quote, mode, dailyChange);
    return true;
  }

  function renderSummary() {
    var liveValues = Array.from(quoteBySymbol.values());
    var changes = liveValues.map(function (item) { return item.totalChange; }).filter(Number.isFinite);
    if (!changes.length) return;

    var sorted = changes.slice().sort(function (a, b) { return a - b; });
    var rising = changes.filter(function (value) { return value > 0; }).length;
    var middle = Math.floor(sorted.length / 2);
    var median = sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    var strongest = Math.max.apply(null, changes);
    var risingEl = document.getElementById('tc-summary-rising');
    var countEl = document.getElementById('tc-summary-rising-count');
    var medianEl = document.getElementById('tc-summary-median');
    var strongestEl = document.getElementById('tc-summary-strongest');
    var modeEl = document.getElementById('tc-summary-mode');
    var firstLive = liveValues[0];

    if (risingEl) risingEl.textContent = fmt(rising / changes.length * 100, 1) + '%';
    if (countEl) countEl.textContent = rising + '/' + changes.length;
    if (medianEl) { medianEl.textContent = signed(median, 1) + '%'; setDirection(medianEl, median); }
    if (strongestEl) { strongestEl.textContent = signed(strongest, 1) + '%'; setDirection(strongestEl, strongest); }
    if (modeEl && firstLive) {
      var quoted = quoteParts(firstLive.quote);
      var partial = changes.length === cards.length ? '' : ' · 部分实时 ' + changes.length + '/' + cards.length;
      modeEl.textContent = liveMode(firstLive.quote) + '统计 · ' + (quoted ? quoted.time : '—') + partial;
    }
  }

  function rerenderCached() {
    cards.forEach(function (card) {
      var cached = quoteBySymbol.get(card.dataset.liveSymbol);
      if (cached) renderCard(card, cached.quote);
    });
    renderSummary();
  }

  async function refresh() {
    if (fetching || document.hidden) return;
    fetching = true;
    try {
      var symbols = cards.map(function (card) { return card.dataset.liveSymbol; }).join(',');
      var response = await fetch('/api/public/v1/quote?symbols=' + encodeURIComponent(symbols), { cache: 'default' });
      if (!response.ok) return;
      var payload = await response.json();
      var normalized = window.EtfQuote && window.EtfQuote.normalizeQuotePayload(payload);
      if (!normalized || !normalized.ok) return;
      cards.forEach(function (card) {
        var quote = window.EtfQuote.findQuoteItem(normalized, card.dataset.liveSymbol);
        if (quote) renderCard(card, quote);
      });
      renderSummary();
      document.dispatchEvent(new CustomEvent('low-chip-quotes-updated'));
    } catch (error) {
      console.warn('[low-chip-live] quote refresh failed', error);
    } finally {
      fetching = false;
    }
  }

  async function initialize() {
    var calendarReady = false;
    if (window.EtfLivePoll && window.EtfLivePoll.getCalendar) {
      try {
        var calendar = await window.EtfLivePoll.getCalendar('CN_A');
        currentPhase = window.EtfLivePoll.marketPhase('CN_A', calendar);
        calendarReady = true;
      } catch (error) {
        console.warn('[low-chip-live] market calendar unavailable', error);
      }
    }
    if (calendarReady && currentPhase && currentPhase.label !== '今日休市') await refresh();
    if (window.EtfLivePoll && window.EtfLivePoll.startMarketPoll) {
      window.EtfLivePoll.startMarketPoll({
        market: 'CN_A',
        intervalMs: POLL_MS,
        immediate: false,
        tick: async function (context) {
          currentPhase = context.phase;
          await refresh();
        },
        onStatus: function (_text, phase) {
          currentPhase = phase;
          rerenderCached();
        }
      });
    } else {
      window.setInterval(refresh, POLL_MS);
      document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
    }
  }

  void initialize();
})();
