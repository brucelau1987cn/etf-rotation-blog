import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../public/js/futures-delivery-countdown.js', import.meta.url).pathname).href;
const { nextCffexDelivery, deliveryCountdown, renderDeliveryCountdown } = await import(moduleUrl);

test('countdown uses current Beijing calendar day instead of snapshot time', () => {
  assert.equal(deliveryCountdown('2026-08-21', '2026-08-02'), 19);
  assert.equal(deliveryCountdown('2026-08-21', '2026-08-20'), 1);
  assert.equal(deliveryCountdown('2026-08-21', '2026-08-21'), 0);
});

test('next delivery rolls to next month immediately after expiry', () => {
  assert.equal(nextCffexDelivery('2026-08-21'), '2026-08-21');
  assert.equal(nextCffexDelivery('2026-08-22'), '2026-09-18');
  assert.equal(nextCffexDelivery('2026-12-19'), '2027-01-15');
});

test('countdown markup keeps days white and turns urgent within 7 days', () => {
  assert.equal(renderDeliveryCountdown(0), '今日交割');
  assert.equal(renderDeliveryCountdown(16), '距交割 <span class="delivery-days">16</span> 天');
  assert.equal(renderDeliveryCountdown(7), '距交割 <span class="delivery-days">7</span> 天');
});

test('page wires countdown to a daily browser-side updater', async () => {
  const { readFile } = await import('node:fs/promises');
  const page = await readFile(new URL('../src/pages/futures-compass/index.astro', import.meta.url), 'utf8');
  assert.match(page, /id="delivery-date"/);
  assert.match(page, /id="delivery-countdown"/);
  assert.match(page, /futures-delivery-countdown\.js/);
  assert.match(page, /delivery-countdown\.is-urgent/);
  assert.match(page, /is:global/);
  assert.match(page, /font-size:1\.45rem/);
  assert.match(page, /delivery-days/);
  assert.match(page, /span class=\"delivery-days\"/);
});
