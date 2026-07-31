/**
 * Shared live-price poll helper.
 * - pauses hidden tabs
 * - optionally gates requests with the D1 market calendar
 * - supports a 15-second countdown and exchange-session status text
 */
(function (global) {
  'use strict';

  const MARKET_CONFIG = {
    CN_A: { timezone: 'Asia/Shanghai' },
    HK: { timezone: 'Asia/Hong_Kong' },
    US: { timezone: 'America/New_York' },
  };
  const calendarCache = new Map();

  const localParts = (timezone) => Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date()).filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));

  const secondsAt = (h, m, s = 0) => h * 3600 + m * 60 + s;
  const clock = (timezone) => {
    const p = localParts(timezone);
    return {
      date: `${p.year}-${p.month}-${p.day}`,
      weekday: p.weekday,
      seconds: Number(p.hour) * 3600 + Number(p.minute) * 60 + Number(p.second),
    };
  };
  const isoTimeSeconds = (value, timezone) => {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    const p = localParts(timezone);
    const target = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
    return Number(target.hour) * 3600 + Number(target.minute) * 60 + Number(target.second);
  };
  const formatNext = (value, timezone) => {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date).replace(/\//g, '-');
  };

  async function getCalendar(market, force = false) {
    const cached = calendarCache.get(market);
    if (!force && cached && Date.now() - cached.fetchedAt < 5 * 60_000) return cached.data;
    const response = await fetch(`/api/public/v1/market-session?market=${encodeURIComponent(market)}&t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`market calendar HTTP ${response.status}`);
    const data = await response.json();
    calendarCache.set(market, { data, fetchedAt: Date.now() });
    return data;
  }

  function marketPhase(market, calendar) {
    const config = MARKET_CONFIG[market];
    if (!config) return { active: true, label: '实时行情' };
    const now = clock(config.timezone);
    const row = calendar?.session;
    if (!row || row.trade_date !== now.date || Number(row.is_open) !== 1) {
      return { active: false, label: '今日休市', next: calendar?.next_open_session?.open_at || null };
    }
    const t = now.seconds;
    if (market === 'CN_A') {
      if (t >= secondsAt(9, 15) && t < secondsAt(9, 25)) return { active: true, label: '开盘竞价' };
      if (t >= secondsAt(9, 25) && t < secondsAt(9, 30)) return { active: false, label: '等待开盘', resume: '09:30' };
      if (t >= secondsAt(9, 30) && t < secondsAt(11, 30)) return { active: true, label: '盘中实时' };
      if (t >= secondsAt(11, 30) && t < secondsAt(13, 0)) return { active: false, label: '午间休市', resume: '13:00' };
      if (t >= secondsAt(13, 0) && t < secondsAt(14, 57)) return { active: true, label: '盘中实时' };
      if (t >= secondsAt(14, 57) && t <= secondsAt(15, 0)) return { active: true, label: '收盘竞价' };
      if (t < secondsAt(9, 15)) return { active: false, label: '等待竞价', resume: '09:15' };
      return { active: false, label: '已收盘', next: calendar?.next_open_session?.open_at || null };
    }
    if (market === 'HK') {
      if (t >= secondsAt(9, 0) && t < secondsAt(9, 30)) return { active: true, label: '开盘竞价' };
      if (t >= secondsAt(9, 30) && t < secondsAt(12, 0)) return { active: true, label: '盘中实时' };
      if (t >= secondsAt(12, 0) && t < secondsAt(13, 0)) return { active: false, label: '午间休市', resume: '13:00' };
      if (t >= secondsAt(13, 0) && t < secondsAt(16, 0)) return { active: true, label: '盘中实时' };
      if (t >= secondsAt(16, 0) && t <= secondsAt(16, 10)) return { active: true, label: '收盘竞价' };
      if (t < secondsAt(9, 0)) return { active: false, label: '等待竞价', resume: '09:00' };
      return { active: false, label: '已收盘', next: calendar?.next_open_session?.open_at || null };
    }
    const open = isoTimeSeconds(row.open_at, config.timezone) ?? secondsAt(9, 30);
    const close = isoTimeSeconds(row.close_at, config.timezone) ?? secondsAt(16, 0);
    if (t >= open && t <= close) return { active: true, label: row.session_type === 'early_close' ? '提前收市交易' : '盘中实时' };
    if (t < open) return { active: false, label: '等待开盘', resume: String(row.open_at || '').slice(11, 16) || '09:30' };
    return { active: false, label: '已收盘', next: calendar?.next_open_session?.open_at || null };
  }

  function statusText(phase, countdown, calendar, market) {
    const timezone = MARKET_CONFIG[market]?.timezone || 'UTC';
    const timezoneLabel = market === 'US' ? '美东时间 ' : '';
    if (phase.active) return `${phase.label} · ${Math.max(1, countdown)}s 后刷新`;
    if (phase.resume) return `${phase.label} · ${timezoneLabel}${phase.resume}恢复`;
    if (phase.next) {
      let next = formatNext(phase.next, timezone);
      if (market === 'CN_A') next = `${next.slice(0, 5)} 09:15`;
      if (market === 'HK') next = `${next.slice(0, 5)} 09:00`;
      return `${phase.label} · ${timezoneLabel}${next}恢复`;
    }
    if (!calendar) return '交易日历连接中';
    return phase.label;
  }

  function startMarketPoll(options) {
    const market = String(options?.market || 'CN_A').toUpperCase();
    const intervalMs = Math.max(5000, Number(options?.intervalMs) || 15000);
    const tick = options?.tick;
    const onStatus = typeof options?.onStatus === 'function' ? options.onStatus : null;
    if (typeof tick !== 'function') throw new Error('startMarketPoll requires options.tick');
    let timer = null;
    let calendar = null;
    let calendarFetchedAt = 0;
    let nextRunAt = 0;
    let inFlight = false;
    let stopped = false;

    const paint = () => {
      if (!onStatus) return;
      if (document.hidden) return onStatus('页面后台暂停', { active: false, hidden: true });
      const phase = marketPhase(market, calendar);
      const countdown = Math.ceil(Math.max(0, nextRunAt - Date.now()) / 1000);
      onStatus(statusText(phase, countdown, calendar, market), { ...phase, countdown, calendar });
    };
    const refreshCalendar = async (force = false) => {
      if (!force && calendar && Date.now() - calendarFetchedAt < 5 * 60_000) return;
      try {
        calendar = await getCalendar(market, force);
        calendarFetchedAt = Date.now();
      } catch (error) {
        console.warn('market calendar load failed', error);
        calendar = null;
      }
    };
    const run = async () => {
      if (stopped || document.hidden || inFlight) return;
      await refreshCalendar();
      const phase = marketPhase(market, calendar);
      if (!phase.active || Date.now() < nextRunAt) return paint();
      inFlight = true;
      try {
        await tick({ phase, calendar });
        nextRunAt = Date.now() + intervalMs;
      } catch (error) {
        console.warn('market live poll tick failed', error);
        nextRunAt = Date.now() + intervalMs;
      } finally {
        inFlight = false;
        paint();
      }
    };
    const pulse = () => { void run(); paint(); };
    const onVisible = () => {
      if (!document.hidden) {
        nextRunAt = 0;
        void refreshCalendar(true).then(run);
      }
      paint();
    };
    void refreshCalendar(true).then(() => {
      if (options?.immediate !== false) void run();
      else { nextRunAt = Date.now() + intervalMs; paint(); }
    });
    timer = global.setInterval(pulse, 1000);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      if (timer) global.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }

  function startLivePoll(options) {
    const intervalMs = Math.max(1000, Number(options?.intervalMs) || 30000);
    const tick = options?.tick;
    if (typeof tick !== 'function') throw new Error('startLivePoll requires options.tick');
    let timer = null;
    let inFlight = false;
    const run = async () => {
      if (document.hidden || inFlight) return;
      inFlight = true;
      try { await tick(); }
      catch (error) { console.warn('live poll tick failed', error); }
      finally { inFlight = false; }
    };
    if (options?.immediate !== false) void run();
    timer = global.setInterval(run, intervalMs);
    const onVisible = () => { if (!document.hidden) void run(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      if (timer) global.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }

  global.EtfLivePoll = { startLivePoll, startMarketPoll, getCalendar, marketPhase };
})(typeof window !== 'undefined' ? window : globalThis);
