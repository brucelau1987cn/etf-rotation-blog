#!/usr/bin/env node
/**
 * Keep public/js/normalize-quote-payload.js aligned with src/lib/normalizeQuotePayload.mjs.
 * The browser file is an IIFE mirror; this script regenerates it from the ESM source
 * by re-reading the ESM file and writing the known IIFE wrapper with the same functions.
 *
 * For now we verify both files export the same function names and run a lightweight
 * behavior parity check by dynamic-importing the ESM module and evaluating the IIFE.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const esmPath = join(root, 'src/lib/normalizeQuotePayload.mjs');
const iifePath = join(root, 'public/js/normalize-quote-payload.js');

const esm = await import(pathToFileURL(esmPath).href);
const iifeSource = readFileSync(iifePath, 'utf8');
const sandbox = { window: {}, globalThis: {} };
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.runInNewContext(iifeSource, sandbox);
const iife = sandbox.EtfQuote || sandbox.window.EtfQuote;

assert.ok(iife, 'IIFE must attach EtfQuote');
for (const name of ['normalizeQuotePayload', 'findQuoteItem', 'aShareSymbolsParam', 'bareSymbol', 'quoteMapByCode']) {
  assert.equal(typeof esm[name], 'function', `ESM missing ${name}`);
  assert.equal(typeof iife[name], 'function', `IIFE missing ${name}`);
}

const sample = {
  status: 'ok',
  source: 'tencent',
  quotes: {
    '600021': { symbol: '600021.SH', sec_code: 'sh600021', price: 14.21, change_percent: -7.37, quote_time: '2026-07-25T15:00:00+08:00' },
    'XLC.AM': { symbol: 'XLC.AM', sec_code: 'usXLC', price: 106.3, change_percent: 0.87, quote_time: '2026-07-25T09:20:49.350Z' },
  },
};

const a = esm.normalizeQuotePayload(sample);
const b = iife.normalizeQuotePayload(sample);
const slim = (items) => items.map((x) => ({ symbol: x.symbol, code: x.code, price: x.price, change_percent: x.change_percent }));
assert.equal(JSON.stringify(slim(a.items)), JSON.stringify(slim(b.items)));
assert.equal(esm.findQuoteItem(a, '600021')?.price, 14.21);
assert.equal(iife.findQuoteItem(b, 'XLC')?.price, 106.3);
assert.equal(esm.aShareSymbolsParam(['600021', '159915']), '600021.SH,159915.SZ');
assert.equal(iife.aShareSymbolsParam(['600021', '159915']), '600021.SH,159915.SZ');

console.log('quote adapter parity OK:', iifePath);
