import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const moduleUrl = pathToFileURL(new URL('../public/js/futures-delivery-countdown.js', import.meta.url).pathname).href;
const { nextCffexDelivery, deliveryCountdown } = await import(moduleUrl);

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

test('page wires countdown to a daily browser-side updater', async () => {
  const { readFile } = await import('node:fs/promises');
  const page = await readFile(new URL('../src/pages/futures-compass/index.astro', import.meta.url), 'utf8');
  assert.match(page, /id="delivery-date"/);
  assert.match(page, /id="delivery-countdown"/);
  assert.match(page, /futures-delivery-countdown\.js/);
});
