/**
 * MiniMax token usage dashboard client.
 * Reads API base from [data-token-dashboard][data-api-base].
 */
(function () {
  const root = document.querySelector('[data-token-dashboard]');
  if (!root) return;

  const API_BASE = (root.dataset.apiBase || 'https://minimax.peekabo.cc').replace(/\/$/, '');
  const $ = (s) => document.querySelector(s);
  const fmtPct = (x) => (x == null ? '—' : x + '%');
  const fmtNum = (x) => {
    if (x == null || x === '') return '—';
    if (typeof x === 'string' && /[a-zA-Z]/.test(x)) return x;
    const n = Number(x);
    if (Number.isNaN(n)) return String(x);
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(2) + 'K';
    return n.toLocaleString();
  };
  const countdownTargets = { fiveHour: null, week: null };
  const fmtSeconds = (s) => {
    if (s == null) return '—';
    s = Math.max(0, Math.floor(Number(s)));
    if (Number.isNaN(s)) return '—';
    if (s <= 0) return '已恢复';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (d >= 1) return d + '天' + h + '小时' + m + '分';
    if (h >= 1) return h + '小时' + m + '分' + sec + '秒';
    return m + '分' + sec + '秒';
  };
  const normalizeSeconds = (v) => {
    if (v == null || v === '') return null;
    const n = Number(v);
    if (Number.isNaN(n)) return null;
    // MiniMax remains_time / weekly_remains_time 返回毫秒（> 1000000 即判定为毫秒）。
    return n > 1000000 ? Math.floor(n / 1000) : Math.floor(n);
  };
  const setCountdown = (key, selector, seconds) => {
    const el = $(selector);
    if (!el) return;
    if (seconds == null || Number.isNaN(Number(seconds))) {
      countdownTargets[key] = null;
      el.textContent = '等待平台释放';
      return;
    }
    countdownTargets[key] = Date.now() + seconds * 1000;
    el.textContent = '恢复倒计时：' + fmtSeconds(seconds);
  };
  const tickCountdowns = () => {
    if (countdownTargets.fiveHour) {
      const diff = (countdownTargets.fiveHour - Date.now()) / 1000;
      if (diff <= 0) {
        $('[data-detail="5h"]').textContent = '已恢复';
        countdownTargets.fiveHour = null;
      } else {
        $('[data-detail="5h"]').textContent = '恢复倒计时：' + fmtSeconds(diff);
      }
    }
    if (countdownTargets.week) {
      const diff = (countdownTargets.week - Date.now()) / 1000;
      if (diff <= 0) {
        $('[data-detail="week"]').textContent = '已恢复';
        countdownTargets.week = null;
      } else {
        $('[data-detail="week"]').textContent = '恢复倒计时：' + fmtSeconds(diff);
      }
    }
  };
  const secondsToNextMonday = () => {
    const now = new Date();
    const next = new Date(now);
    const day = now.getDay();
    const daysUntilMonday = day === 0 ? 1 : 8 - day;
    next.setDate(now.getDate() + daysUntilMonday);
    next.setHours(0, 0, 0, 0);
    return Math.max(0, Math.floor((next.getTime() - now.getTime()) / 1000));
  };
  const fmtTs = (s) => {
    if (!s) return '—';
    const d = new Date(Number(s) * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  function setQuotaLevel(card, pct) {
    card.classList.remove('critical', 'warn', 'ok', 'neutral');
    if (pct == null) {
      card.classList.add('neutral');
      return;
    }
    if (pct < 20) card.classList.add('critical');
    else if (pct < 50) card.classList.add('warn');
    else card.classList.add('ok');
  }

  function render(data) {
    const errBox = $('#err-box');
    errBox.style.display = 'none';
    errBox.innerHTML = '';
    if (!data || data.error) {
      errBox.style.display = 'block';
      errBox.innerHTML = '❌ 拉取失败：<code>' + (data?.error || 'no data') + '</code>';
      return;
    }

    const meta = $('#meta-status');
    meta.innerHTML = '✅ ' + fmtTs(data.fetched_at) + ' 更新';

    const summary = data.endpoints.usage_summary || {};
    const remains = data.endpoints.coding_plan_remains || {};
    const plan = data.endpoints.token_plan_usage || {};

    const total = summary.total_token_consumed ?? summary.total_tokens ?? plan.total;
    const totalCard = $('[data-quota="total"]').closest('.quota');
    $('[data-quota="total"]').textContent = fmtNum(total);
    totalCard.classList.remove('critical', 'warn', 'ok', 'neutral');
    totalCard.classList.add('neutral');

    const rank = summary.usage_ranking_percent;
    const rankCard = $('[data-quota="rank"]').closest('.quota');
    $('[data-quota="rank"]').textContent = rank == null ? '—' : ('前 ' + rank.toFixed(1) + '%');
    rankCard.classList.remove('critical', 'warn', 'ok', 'neutral');
    if (rank != null && rank < 20) rankCard.classList.add('ok');
    else if (rank != null && rank > 50) rankCard.classList.add('warn');
    else rankCard.classList.add('neutral');

    const models = remains.model_remains || [];
    const general = models.find((m) => /general/i.test(m.model_name || '')) || models[0];
    if (general) {
      const p5 = general.current_interval_remaining_percent;
      const pw = general.current_weekly_remaining_percent;

      const card5 = $('[data-quota="5h"]').closest('.quota');
      const intervalSeconds = normalizeSeconds(
        general.current_interval_remains_seconds
          ?? general.current_interval_reset_seconds
          ?? general.remains_time,
      );
      $('[data-quota="5h"]').textContent = fmtPct(p5);
      setCountdown('fiveHour', '[data-detail="5h"]', intervalSeconds);
      setQuotaLevel(card5, p5);

      const cardW = $('[data-quota="week"]').closest('.quota');
      const weeklySeconds = normalizeSeconds(
        general.current_weekly_remains_seconds
          ?? general.current_weekly_reset_seconds
          ?? general.weekly_remains_time,
      ) ?? secondsToNextMonday();
      $('[data-quota="week"]').textContent = fmtPct(pw);
      setCountdown('week', '[data-detail="week"]', weeklySeconds);
      setQuotaLevel(cardW, pw);
    } else {
      $('[data-quota="5h"]').textContent = '—';
      $('[data-detail="5h"]').textContent = '恢复：—';
      $('[data-quota="week"]').textContent = '—';
      $('[data-detail="week"]').textContent = '恢复：—';
    }
  }

  async function load(force) {
    const url = API_BASE + '/api/minimax/usage' + (force ? '?refresh=1' : '');
    $('#btn-refresh').disabled = true;
    $('#btn-cache').disabled = true;
    $('#meta-status').innerHTML = '<span class="loading-dot"></span>拉取中…';
    try {
      const r = await fetch(url, { cache: 'no-store' });
      const data = await r.json();
      render(data);
    } catch (e) {
      $('#err-box').style.display = 'block';
      $('#err-box').innerHTML = '❌ 网络错误：<code>' + e.message + '</code>';
      $('#meta-status').textContent = '❌ 失败';
    } finally {
      $('#btn-refresh').disabled = false;
      $('#btn-cache').disabled = false;
    }
  }

  load(false);
  setInterval(() => load(false), 90_000);
  setInterval(tickCountdowns, 1000);
  $('#btn-refresh').addEventListener('click', () => load(true));
  $('#btn-cache').addEventListener('click', () => load(false));
})();
