/**
 * Market clock: network time calibration + session state.
 * Targets [data-market-clock] nodes rendered by MarketClock.astro.
 */
(function () {
  const clocks = [...document.querySelectorAll('[data-market-clock]')];
  if (!clocks.length) return;

  let offsetMs = 0;
  let synchronized = false;

  const partsFor = (date, timeZone) => Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  );

  const marketState = (market, parts) => {
    if (['Sat', 'Sun'].includes(parts.weekday)) return { text: '周末休市', tone: '' };
    const minutes = Number(parts.hour) * 60 + Number(parts.minute);
    if (market === 'a-share') {
      if ((minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900)) {
        return { text: 'A股常规交易中', tone: 'open' };
      }
      if (minutes >= 690 && minutes < 780) return { text: 'A股午间休市', tone: 'break' };
      if (minutes < 570) return { text: 'A股等待开盘', tone: '' };
      return { text: 'A股已收盘', tone: '' };
    }
    if (minutes >= 570 && minutes < 960) return { text: '美股常规交易中', tone: 'open' };
    if (minutes < 570) return { text: '美股等待开盘', tone: '' };
    return { text: '美股已收盘', tone: '' };
  };

  const render = () => {
    const date = new Date(Date.now() + offsetMs);
    for (const clock of clocks) {
      const zone = clock.dataset.timeZone;
      const parts = partsFor(date, zone);
      const hour = Number(parts.hour);
      const minute = Number(parts.minute);
      const second = Number(parts.second);
      clock.querySelector('[data-clock-time]').textContent = `${parts.hour}:${parts.minute}:${parts.second}`;
      clock.querySelector('[data-clock-date]').textContent = `${parts.year}-${parts.month}-${parts.day} · ${parts.weekday}`;
      clock.querySelector('[data-clock-hour]').style.transform = `translateX(-50%) rotate(${(hour % 12) * 30 + minute * 0.5}deg)`;
      clock.querySelector('[data-clock-minute]').style.transform = `translateX(-50%) rotate(${minute * 6 + second * 0.1}deg)`;
      clock.querySelector('[data-clock-second]').style.transform = `translateX(-50%) rotate(${second * 6}deg)`;
      const state = marketState(clock.dataset.market, parts);
      const stateNode = clock.querySelector('[data-clock-market-state]');
      stateNode.textContent = state.text;
      stateNode.className = state.tone;
      const syncNode = clock.querySelector('[data-clock-sync]');
      syncNode.textContent = synchronized ? '网络已校时' : '本机时间';
      syncNode.classList.toggle('synced', synchronized);
    }
  };

  const calibrate = async () => {
    const started = Date.now();
    try {
      const response = await fetch(`${location.pathname}?clock-sync=${started}`, {
        method: 'HEAD',
        cache: 'no-store',
      });
      const serverDate = response.headers.get('date');
      const ended = Date.now();
      const serverMs = serverDate ? Date.parse(serverDate) : NaN;
      if (!response.ok || !Number.isFinite(serverMs)) throw new Error('network time unavailable');
      offsetMs = serverMs - Math.round((started + ended) / 2);
      synchronized = true;
    } catch {
      offsetMs = 0;
      synchronized = false;
    }
    render();
  };

  render();
  void calibrate();
  window.setInterval(render, 1000);
  window.setInterval(calibrate, 10 * 60 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void calibrate();
  });
})();
