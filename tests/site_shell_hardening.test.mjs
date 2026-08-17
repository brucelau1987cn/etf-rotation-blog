import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const headers = await readFile(new URL('../public/_headers', import.meta.url), 'utf8');
const a11y = await readFile(new URL('../public/js/site-a11y.js', import.meta.url), 'utf8');
const header = await readFile(new URL('../src/components/Header.astro', import.meta.url), 'utf8');

test('CSP permits the Cloudflare Web Analytics script and beacon', () => {
  assert.match(headers, /script-src[^\n]*https:\/\/static\.cloudflareinsights\.com/);
  assert.match(headers, /connect-src[^\n]*https:\/\/cloudflareinsights\.com/);
});

test('CSP permits the TradingView technical-analysis web component', () => {
  assert.match(headers, /script-src[^\n]*https:\/\/widgets\.tradingview-widget\.com/);
  assert.match(headers, /frame-src[^\n]*https:\/\/widgets\.tradingview-widget\.com/);
  assert.match(headers, /connect-src[^\n]*https:\/\/widgets\.tradingview-widget\.com/);
});

const usCompass = await readFile(new URL('../src/pages/us-compass.astro', import.meta.url), 'utf8');

const runA11y = (initialMainId = '') => {
  let onReady;
  let skipHref = '#main-content';
  const attrs = new Map();
  const main = {
    id: initialMainId,
    hasAttribute: (name) => attrs.has(name),
    setAttribute: (name, value) => attrs.set(name, value),
  };
  const skip = { setAttribute: (name, value) => { if (name === 'href') skipHref = value; } };
  const document = {
    addEventListener: (_name, callback) => { onReady = callback; },
    querySelector: (selector) => selector === 'main' ? main : selector === '.skip-link' ? skip : null,
  };
  vm.runInNewContext(a11y, { document });
  onReady();
  return { mainId: main.id, tabindex: attrs.get('tabindex'), skipHref };
};

test('shared skip link creates and targets a focusable main landmark', () => {
  assert.deepEqual(runA11y(), { mainId: 'main-content', tabindex: '-1', skipHref: '#main-content' });
});

test('shared skip link preserves and targets an existing main id', () => {
  assert.deepEqual(runA11y('top'), { mainId: 'top', tabindex: '-1', skipHref: '#top' });
});

test('header authentication controls meet touch target sizing', () => {
  assert.match(header, /href="#main-content"/);
  assert.match(header, /\/js\/site-a11y\.js/);
  assert.match(header, /\.user-login-link\s*\{[^}]*min-height:\s*44px/s);
  assert.match(header, /\.user-trigger\s*\{[^}]*min-height:\s*44px/s);
  assert.match(header, /\.user-item\s*\{[^}]*min-height:\s*44px/s);
});

test('global US compass stylesheet uses plain market-clock selectors', () => {
  assert.match(usCompass, /<style is:global>/);
  assert.doesNotMatch(usCompass, /\.meta-line\s+:global\(\.market-clock\)/);
  assert.match(usCompass, /\.meta-line\s+\.market-clock/);
});
