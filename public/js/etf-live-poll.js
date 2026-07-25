/**
 * Shared live-poll helper for ETF pages.
 * Pauses while the tab is hidden; refreshes immediately when visible again.
 */
(function (global) {
  'use strict';

  function startLivePoll(options) {
    const intervalMs = Math.max(1000, Number(options?.intervalMs) || 30000);
    const tick = options?.tick;
    if (typeof tick !== 'function') throw new Error('startLivePoll requires options.tick');

    let timer = null;
    let inFlight = false;

    const run = async () => {
      if (document.hidden || inFlight) return;
      inFlight = true;
      try {
        await tick();
      } catch (error) {
        console.warn('live poll tick failed', error);
      } finally {
        inFlight = false;
      }
    };

    if (options?.immediate !== false) void run();
    timer = global.setInterval(run, intervalMs);
    const onVisible = () => {
      if (!document.hidden) void run();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      if (timer) global.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }

  global.EtfLivePoll = { startLivePoll };
})(typeof window !== 'undefined' ? window : globalThis);
