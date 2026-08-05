const DAY_MS = 86400000;

const parseIsoDay = (value) => {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
};

const isoDay = (value) => {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : '';
};

const thirdFriday = (year, monthIndex) => {
  const firstWeekday = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
  const firstFriday = 1 + ((5 - firstWeekday + 7) % 7);
  return new Date(Date.UTC(year, monthIndex, firstFriday + 14));
};

export const nextCffexDelivery = (todayIso) => {
  const timestamp = parseIsoDay(todayIso);
  if (timestamp === null) return '';
  const today = new Date(timestamp);
  let delivery = thirdFriday(today.getUTCFullYear(), today.getUTCMonth());
  if (timestamp > delivery.getTime()) {
    delivery = thirdFriday(today.getUTCFullYear(), today.getUTCMonth() + 1);
  }
  return delivery.toISOString().slice(0, 10);
};

export const deliveryCountdown = (deliveryIso, todayIso) => {
  const delivery = parseIsoDay(deliveryIso);
  const today = parseIsoDay(todayIso);
  if (delivery === null || today === null) return null;
  return Math.max(0, Math.round((delivery - today) / DAY_MS));
};

export const beijingIsoDay = (now = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(now);

export const renderDeliveryCountdown = (days) => {
  if (days === null || days === undefined || !Number.isFinite(Number(days))) return '';
  const n = Math.max(0, Number(days));
  if (n === 0) return '今日交割';
  // Use span (not strong): global.css paints all strong black and would override white days.
  return `距交割 <span class="delivery-days">${n}</span> 天`;
};

export const updateDeliveryCountdown = (now = new Date()) => {
  const dateNode = document.getElementById('delivery-date');
  const countdownNode = document.getElementById('delivery-countdown');
  if (!dateNode || !countdownNode) return;
  const today = beijingIsoDay(now);
  const delivery = nextCffexDelivery(today) || isoDay(dateNode.dataset.deliveryDate);
  if (!delivery) return;
  dateNode.textContent = delivery;
  dateNode.dataset.deliveryDate = delivery;
  const days = deliveryCountdown(delivery, today);
  if (days === null) return;
  countdownNode.dataset.days = String(days);
  countdownNode.classList.toggle('is-urgent', days <= 7);
  countdownNode.innerHTML = renderDeliveryCountdown(days);
};

if (typeof document !== 'undefined') {
  updateDeliveryCountdown();
  const timer = window.setInterval(() => updateDeliveryCountdown(), 60 * 60 * 1000);
  window.addEventListener('pageshow', () => updateDeliveryCountdown());
  window.addEventListener('beforeunload', () => window.clearInterval(timer), { once: true });
}
